"""Writing the lock again.

`update` decides two things: which revision each source is locked at, and which
nodes survive. Neither decision needs a network, because the two things that do
are passed in. The one place the real `nix` output format is checked is
`parse_prefetch`, against output captured from a real run.
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


def prefetcher(_owner: str, _repo: str, rev: str) -> dict:
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


# -- reading a url ----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/who/what.git",
        "https://github.com/who/what",
        "https://github.com/who/what/",
    ],
)
def test_github_slug_reads_owner_and_repository(url: str) -> None:
    assert update.github_slug("n", url) == ("who", "what")


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/who/what.git",
        "git@github.com:who/what.git",
        "https://github.com/who",
        "https://github.com/who/what/deeper",
    ],
)
def test_github_slug_refuses_what_a_lock_node_cannot_hold(url: str) -> None:
    with pytest.raises(UmbrellaError):
        update.github_slug("n", url)


# -- which revision ---------------------------------------------------------


def test_a_submodule_takes_the_pointer_and_asks_no_remote() -> None:
    sources, changes = update.rewrite(
        spec={"sub1": SPEC["sub1"]},
        pointers={"sub1": A},
        existing={},
        head_of=refuse,
        prefetch=prefetcher,
    )

    assert sources["sub1"]["rev"] == A
    assert [(c.name, c.was, c.now, c.where) for c in changes] == [
        ("sub1", None, A, "pointer")
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


def test_update_locks_the_recorded_pointer_and_not_the_working_copy(
    checkout: Checkout, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """The whole command, with nix faked out.

    The submodule is moved on and committed but not landed, so its HEAD and its
    pointer differ. The lock has to take the pointer: nothing else is public.
    """
    monkeypatch.setattr(
        nixcli,
        "spec",
        lambda _workdir: {
            "sub1": {"url": "https://github.com/who/sub1.git", "branch": "main"},
        },
    )
    monkeypatch.setattr(
        nixcli, "prefetch", lambda _workdir, owner, repo, rev: node(rev, owner, repo)
    )

    recorded = checkout.recorded("sub1")
    checkout.edit("sub1", "v2")
    head = checkout.commit("sub1", "v2")
    assert head != recorded

    assert checkout.cli("update") == 0

    assert capsys.readouterr().out.count("(pointer)") == 1
    assert str(lock.read(checkout.path)["sub1"]) == recorded


def test_update_dry_run_writes_nothing(
    checkout: Checkout, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    monkeypatch.setattr(
        nixcli,
        "spec",
        lambda _workdir: {
            "sub1": {"url": "https://github.com/who/sub1.git", "branch": "main"},
        },
    )
    monkeypatch.setattr(
        nixcli, "prefetch", lambda _workdir, owner, repo, rev: node(rev, owner, repo)
    )

    assert checkout.cli("update", "--dry-run") == 0

    assert "--dry-run" in capsys.readouterr().out
    assert not (checkout.path / lock.PATH).exists()


def test_update_says_so_when_nothing_moved(
    checkout: Checkout, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    monkeypatch.setattr(
        nixcli,
        "spec",
        lambda _workdir: {
            "sub1": {"url": "https://github.com/who/sub1.git", "branch": "main"},
        },
    )
    monkeypatch.setattr(
        nixcli, "prefetch", lambda _workdir, owner, repo, rev: node(rev, owner, repo)
    )

    assert checkout.cli("update") == 0
    capsys.readouterr()
    assert checkout.cli("update") == 0

    assert "nothing moved" in capsys.readouterr().out


def test_update_clears_the_row_that_status_shows(
    checkout: Checkout, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """The two commands are one loop: status reports the drift, update ends it."""
    monkeypatch.setattr(
        nixcli,
        "spec",
        lambda _workdir: {
            "sub1": {"url": "https://github.com/who/sub1.git", "branch": "main"},
        },
    )
    monkeypatch.setattr(
        nixcli, "prefetch", lambda _workdir, owner, repo, rev: node(rev, owner, repo)
    )

    stale = checkout.recorded("sub1")
    assert checkout.cli("update") == 0
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    assert checkout.cli("land", "-m", "bump sub1") == 0
    capsys.readouterr()

    assert checkout.cli("status") == 0
    assert f"lock-names-{stale[:8]}" in capsys.readouterr().out

    assert checkout.cli("update") == 0
    capsys.readouterr()
    assert checkout.cli("status") == 0
    assert "lock-names" not in capsys.readouterr().out


def test_land_names_the_source_whose_lock_it_left_behind(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """land is what creates the drift, so it is where saying so costs least."""
    lock.write(checkout.path, {"sub1": node(checkout.recorded("sub1"))})
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land", "-m", "bump sub1") == 0

    out = capsys.readouterr().out
    assert "umbrella update sub1" in out


def test_land_says_nothing_when_there_is_no_lock(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land", "-m", "bump sub1") == 0

    assert "umbrella update" not in capsys.readouterr().out


def test_land_says_nothing_when_the_lock_keeps_up(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lock already naming the commit being landed is not a drift."""
    checkout.edit("sub1", "v2")
    head = checkout.commit("sub1", "v2")
    lock.write(checkout.path, {"sub1": node(head)})

    assert checkout.cli("land", "-m", "bump sub1") == 0

    assert "umbrella update" not in capsys.readouterr().out


def test_update_needs_a_specification(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """There is no nix/sources.nix in the lab, and nix is not in the sandbox.
    The command has to say the first thing before it reaches the second."""
    assert checkout.cli("update") == 1
    assert nixcli.SPEC in capsys.readouterr().err


def test_a_single_project_follows_every_branch(
    lab: Lab, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """No submodules means no pointers, so every source takes its branch."""
    monkeypatch.setattr(
        nixcli,
        "spec",
        lambda _workdir: {
            "dep": {"url": "https://github.com/who/dep.git", "branch": "main"},
        },
    )
    monkeypatch.setattr(
        nixcli, "prefetch", lambda _workdir, owner, repo, rev: node(rev, owner, repo)
    )
    monkeypatch.setattr("umbrella.gitcli.remote_head", lambda _cwd, _url, _branch: A)

    single = Checkout(lab.root / "_seed-sub1")

    assert single.cli("update") == 0

    assert "(main)" in capsys.readouterr().out
    assert str(lock.read(single.path)["dep"]) == A
