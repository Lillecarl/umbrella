"""Writing the lock again.

`update` decides two things: which revision each source is locked at, and which
nodes survive. Neither decision needs a network, because the two things that do
are passed in. The one place the real `nix` output format is checked is
`parse_prefetch`, against output captured from a real run.

`update` follows the branch each source declares. `land` passes in what it just
pushed. Those are the two arms, and they are two commands on purpose: one
follows the forge, the other publishes this disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import Checkout, Lab
from umbrella import lock, nixcli, update
from umbrella.model import UmbrellaError

# What `nix flake prefetch --json github:numtide/treefmt-nix/27b3b12a...` printed.
# It carries no narHash under `locked`: the hash is at the top level, which is
# why parse_prefetch looks in both places.
REAL_PREFETCH = json.dumps(
    {
        "hash": "sha256-WSFCsDSE5ffgD2MqzkM2CYjeFiKhRF/dJUN8uedb6YE=",
        "locked": {
            "lastModified": 1786901030,
            "owner": "numtide",
            "repo": "treefmt-nix",
            "rev": "27b3b12a8e6375f28ebe122f07d230ca5459bbfa",
            "type": "github",
        },
        "original": {
            "owner": "numtide",
            "repo": "treefmt-nix",
            "rev": "27b3b12a8e6375f28ebe122f07d230ca5459bbfa",
            "type": "github",
        },
        "storePath": "/nix/store/7sjy8935654h3220n5gbaclf5nfw4nzx-source",
    }
)


def node(rev: str, owner: str = "who", repo: str = "what") -> dict:
    return {
        "type": "github",
        "owner": owner,
        "repo": repo,
        "rev": rev,
        "lastModified": 1,
        "narHash": f"sha256-{rev[:6]}",
    }


def prefetcher(_url: str, rev: str) -> dict:
    """Stands in for nix. It only has to give the revision back in a node."""
    return node(rev)


def refuse(_url: str, _branch: str) -> str | None:
    raise AssertionError("this run should not have asked a remote for anything")


SPEC = {
    "sub1": {"url": "https://github.com/who/sub1.git", "branch": "main"},
    "nixpkgs": {"url": "https://github.com/NixOS/nixpkgs.git", "branch": "unstable"},
}

A = "a" * 40
B = "b" * 40
C = "c" * 40


# -- reading what nix prints ------------------------------------------------


def test_parse_prefetch_takes_the_hash_from_the_top_level() -> None:
    assert nixcli.parse_prefetch(REAL_PREFETCH) == {
        "type": "github",
        "owner": "numtide",
        "repo": "treefmt-nix",
        "rev": "27b3b12a8e6375f28ebe122f07d230ca5459bbfa",
        "lastModified": 1786901030,
        "narHash": "sha256-WSFCsDSE5ffgD2MqzkM2CYjeFiKhRF/dJUN8uedb6YE=",
    }


def test_parse_prefetch_prefers_the_locked_hash() -> None:
    """A reference that already carried one is answered with that one."""
    raw = json.dumps({"hash": "sha256-top", "locked": {"narHash": "sha256-locked"}})

    assert nixcli.parse_prefetch(raw)["narHash"] == "sha256-locked"


def test_parse_prefetch_refuses_output_with_no_hash() -> None:
    with pytest.raises(nixcli.NixError):
        nixcli.parse_prefetch(json.dumps({"locked": {"rev": A}}))


# -- building a reference --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/who/what.git",
        "https://github.com/who/what",
        "https://github.com/who/what/",
        "git@github.com:who/what.git",
    ],
)
def test_a_github_url_becomes_a_github_reference(url: str) -> None:
    """It has to match what nix/resolve.nix builds from a github node."""
    assert nixcli.reference(url, A) == f"github:who/what/{A}"


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/who/what.git",
        "file:///tmp/what.git",
        "https://github.com/who",
    ],
)
def test_any_other_url_becomes_a_git_reference(url: str) -> None:
    """A source off github is still lockable. resolve.nix reads a git node."""
    assert nixcli.reference(url, A) == f"git+{url}?rev={A}"


# -- which revision ---------------------------------------------------------


def test_a_revision_the_caller_decided_asks_no_remote() -> None:
    sources, changes = update.rewrite(
        spec={"sub1": SPEC["sub1"]},
        pointers={"sub1": A},
        existing={},
        head_of=refuse,
        prefetch=prefetcher,
    )

    assert sources["sub1"]["rev"] == A
    assert [(c.name, c.was, c.now, c.where) for c in changes] == [
        ("sub1", None, A, "landed")
    ]


def test_everything_else_takes_the_head_of_its_branch() -> None:
    asked = []

    def head_of(url: str, branch: str) -> str:
        asked.append((url, branch))
        return B

    sources, changes = update.rewrite(
        spec={"nixpkgs": SPEC["nixpkgs"]},
        pointers={},
        existing={"nixpkgs": node(A)},
        head_of=head_of,
        prefetch=prefetcher,
    )

    assert asked == [("https://github.com/NixOS/nixpkgs.git", "unstable")]
    assert sources["nixpkgs"]["rev"] == B
    assert [(c.was, c.now, c.where) for c in changes] == [(A, B, "unstable")]


def test_a_revision_that_did_not_move_is_not_reported() -> None:
    _, changes = update.rewrite(
        spec={"sub1": SPEC["sub1"]},
        pointers={"sub1": A},
        existing={"sub1": node(A)},
        head_of=refuse,
        prefetch=prefetcher,
    )

    assert changes == []


def test_a_branch_the_remote_does_not_have_stops_the_run() -> None:
    with pytest.raises(UmbrellaError, match="unstable"):
        update.rewrite(
            spec={"nixpkgs": SPEC["nixpkgs"]},
            pointers={},
            existing={},
            head_of=lambda _url, _branch: None,
            prefetch=prefetcher,
        )


# -- which nodes survive ----------------------------------------------------


def test_a_full_run_drops_a_name_the_specification_no_longer_declares() -> None:
    sources, changes = update.rewrite(
        spec=SPEC,
        pointers={"sub1": A},
        existing={"sub1": node(A), "nixpkgs": node(B), "gone": node(C)},
        head_of=lambda _url, _branch: B,
        prefetch=prefetcher,
    )

    assert "gone" not in sources
    dropped = [c for c in changes if c.dropped]
    assert [(c.name, c.was) for c in dropped] == [("gone", C)]


def test_a_run_with_names_leaves_every_other_node_exactly_as_it_was() -> None:
    """The arm people rely on: update one source, rebuild nothing else.

    A limited run was never told about the rest, so it may not drop a node
    either -- not even one the specification no longer declares.
    """
    was = {"sub1": node(A), "nixpkgs": node(B), "gone": node(C)}

    sources, changes = update.rewrite(
        spec=SPEC,
        pointers={"sub1": C},
        existing=was,
        head_of=refuse,
        prefetch=prefetcher,
        names=["sub1"],
    )

    assert sources["nixpkgs"] == was["nixpkgs"]
    assert sources["gone"] == was["gone"]
    assert sources["sub1"]["rev"] == C
    assert [c.name for c in changes] == ["sub1"]


def test_a_name_the_specification_does_not_declare_stops_the_run() -> None:
    with pytest.raises(UmbrellaError, match="nosuch"):
        update.rewrite(
            spec=SPEC,
            pointers={},
            existing={},
            head_of=refuse,
            prefetch=prefetcher,
            names=["nosuch"],
        )


# -- the file ---------------------------------------------------------------


def test_write_then_read_gives_the_same_revisions(tmp_path: Path) -> None:
    lock.write(tmp_path, {"sub1": node(A), "nixpkgs": node(B)})

    assert {name: str(oid) for name, oid in lock.read(tmp_path).items()} == {
        "sub1": A,
        "nixpkgs": B,
    }


def test_write_sorts_the_whole_file(tmp_path: Path) -> None:
    """A tool writes this and a human reads the diff. One moved revision has to
    be one line of it."""
    lock.write(tmp_path, {"zzz": node(B), "aaa": node(A)})
    text = (tmp_path / lock.PATH).read_text()

    assert text.index('"aaa"') < text.index('"zzz"')
    assert text.index('"sources"') < text.index('"version"')
    assert text.endswith("}\n")


# -- through the command line ----------------------------------------------
#
# `nix_free` answers for nix here, from the lab. Every other decision is made
# against the real repositories the lab builds.


def test_update_follows_the_branch_and_not_the_working_copy(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A commit that is only on this disk is not what a clone would get."""
    before = checkout.locked("sub1")
    checkout.edit("sub1", "v2")
    head = checkout.commit("sub1", "v2")
    assert head != before

    assert checkout.cli("update") == 0

    assert "nothing moved" in capsys.readouterr().out
    assert str(lock.read(checkout.path)["sub1"]) == before


def test_update_takes_a_branch_head_somebody_else_pushed(
    lab: Lab, checkout: Checkout
) -> None:
    moved = lab.push_from_elsewhere("sub1", "from somewhere else")

    assert checkout.cli("update", "sub1") == 0

    assert str(lock.read(checkout.path)["sub1"]) == moved


def test_update_dry_run_writes_nothing(
    lab: Lab, checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    before = checkout.locked("sub1")
    lab.push_from_elsewhere("sub1", "from somewhere else")

    assert checkout.cli("update", "--dry-run") == 0

    assert "--dry-run" in capsys.readouterr().out
    assert checkout.locked("sub1") == before


def test_update_says_so_when_nothing_moved(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checkout.cli("update") == 0
    assert "nothing moved" in capsys.readouterr().out


def test_land_writes_the_lock_for_what_it_pushed(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """land is the other arm: it publishes this disk, then locks that."""
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    assert checkout.locked("sub1") == landed
    assert "(landed)" in capsys.readouterr().out


def test_land_leaves_a_source_it_did_not_push_alone(
    lab: Lab, checkout: Checkout
) -> None:
    """A limited run may not drop or move a node it was never told about."""
    moved = lab.push_from_elsewhere("sub2", "from somewhere else")
    before = checkout.locked("sub2")
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    assert checkout.locked("sub2") == before != moved


def test_update_needs_a_specification(
    lab: Lab, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """Without one there is nothing to follow, and it has to say so."""
    from umbrella.nixcli import NixError

    def missing(_workdir):
        raise NixError(f"there is no {nixcli.SPEC} here")

    monkeypatch.setattr(nixcli, "spec", missing)
    checkout = Checkout(lab.clone("nospec"))

    assert checkout.cli("update") == 1
    assert nixcli.SPEC in capsys.readouterr().err


def test_update_refuses_on_a_single_project(single: Checkout) -> None:
    """It locks nothing, so there is nothing to follow."""
    assert single.cli("update") == 1
