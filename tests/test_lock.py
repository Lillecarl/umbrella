"""Reading nix/sources.lock, which is the whole record of what is coordinated.

There used to be two records here, and a row of `status` comparing them: a
submodule pointer in the umbrella's tree and a revision in the lock. They drift,
quietly, and Nix can only see one of them. The submodules are gone, so this file
now tests the one that is left.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import Checkout, Lab, node

from umbrella import lock
from umbrella.model import UmbrellaError


def _write(checkout: Checkout, sources: dict[str, dict]) -> Path:
    directory = checkout.path / "nix"
    directory.mkdir(exist_ok=True)
    file = directory / "sources.lock"
    file.write_text(json.dumps({"version": 1, "sources": sources}, indent=2))
    return file


def _status(checkout: Checkout, capsys: pytest.CaptureFixture[str]) -> str:
    assert checkout.cli("status") == 0
    return capsys.readouterr().out


# -- what a node says -------------------------------------------------------


def test_a_github_node_gives_an_https_url() -> None:
    entry = {"type": "github", "owner": "who", "repo": "what", "rev": "0" * 40}
    assert lock.url("n", entry) == "https://github.com/who/what.git"


def test_a_git_node_gives_its_own_url() -> None:
    entry = {"type": "git", "url": "file:///tmp/what.git", "rev": "0" * 40}
    assert lock.url("n", entry) == "file:///tmp/what.git"


def test_a_node_of_an_unknown_type_is_refused() -> None:
    with pytest.raises(UmbrellaError, match="unknown type"):
        lock.url("n", {"type": "tarball"})


def test_an_entry_with_no_revision_locks_nothing() -> None:
    """A source can be locked by a path or by a hash alone."""
    assert lock.revision({"narHash": "sha256-nothing"}) is None
    assert lock.revision({"rev": "not-a-revision"}) is None


def test_a_source_with_no_revision_is_left_out_of_the_answer() -> None:
    entries = {"a": node("file:///a", "a" * 40), "b": {"narHash": "sha256-x"}}
    assert sorted(lock.revisions(entries)) == ["a"]


# -- what status does with it ----------------------------------------------


def test_a_source_with_no_working_copy_is_listed_and_not_called_a_fault(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pinned checkout has no working copies at all, and that is the default."""
    checkout = Checkout(lab.clone("pinned"))

    out = _status(checkout, capsys)
    assert "2 more from the lock alone" in out
    assert "sub1, sub2" in out


def test_a_working_copy_is_measured_against_the_lock(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout.edit("sub1", "v2")
    ahead = checkout.commit("sub1", "v2")

    out = _status(checkout, capsys)
    line = next(row for row in out.splitlines() if row.startswith("sub1"))
    assert checkout.locked("sub1")[:8] in line
    assert ahead[:8] in line
    assert "ahead-of-lock" in line


def test_a_source_the_lock_does_not_name_is_not_a_source(
    checkout: Checkout,
) -> None:
    """Only the lock decides. A directory nobody locked is somebody's own."""
    (checkout.path / "stray").mkdir()
    assert [s.name for s in checkout.umbrella().sources()] == ["sub1", "sub2"]


# -- a lock nobody can read -------------------------------------------------


def test_an_unreadable_lock_stops_the_command(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    (checkout.path / "nix" / "sources.lock").write_text("{not json")

    assert checkout.cli("status") == 1
    assert "cannot be read" in capsys.readouterr().err


def test_a_lock_of_another_version_stops_the_command(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """Guessing at a format this tool does not know would name the wrong
    revision, which is worse than saying so."""
    _write(checkout, {})
    (checkout.path / "nix" / "sources.lock").write_text(
        json.dumps({"version": 2, "sources": {}})
    )

    assert checkout.cli("status") == 1
    assert "version 2" in capsys.readouterr().err
