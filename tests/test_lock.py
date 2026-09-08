"""The row that compares a Nix lock file with the pointer the umbrella records.

Both name a commit for the same project, and nothing keeps them together. Only
this tool can see the pair: Nix cannot read the git index, and git knows nothing
about the lock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import Checkout, Lab


def _lock(checkout: Checkout, sources: dict[str, dict[str, str]]) -> Path:
    """Write nix/sources.lock into a checkout, in the shape nixidae uses."""
    directory = checkout.path / "nix"
    directory.mkdir(exist_ok=True)
    file = directory / "sources.lock"
    file.write_text(json.dumps({"version": 1, "sources": sources}, indent=2))
    return file


def _status(checkout: Checkout, capsys: pytest.CaptureFixture[str]) -> str:
    assert checkout.cli("status") == 0
    return capsys.readouterr().out


# -- quiet when there is nothing to say ------------------------------------


def test_no_lock_file_says_nothing(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "lock-names" not in _status(checkout, capsys)


def test_a_lock_that_agrees_says_nothing(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    _lock(
        checkout, {name: {"rev": checkout.recorded(name)} for name in ("sub1", "sub2")}
    )

    assert "lock-names" not in _status(checkout, capsys)


def test_a_source_that_is_not_a_submodule_says_nothing(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """Most names in a lock are third parties. They have no pointer to disagree
    with, so they are not a fault and are not reported."""
    _lock(checkout, {"nixpkgs": {"rev": "0" * 40}})

    assert "lock-names" not in _status(checkout, capsys)


def test_an_entry_with_no_revision_says_nothing(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A source can be locked by a path or by a hash alone."""
    _lock(checkout, {"sub1": {"narHash": "sha256-nothing"}})

    assert "lock-names" not in _status(checkout, capsys)


# -- the drift itself -------------------------------------------------------


def test_landing_leaves_the_lock_behind(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """The drift that this row exists for.

    `land` pushes the submodule and records the new pointer. It does not touch
    the lock, so the lock still names the commit from before.
    """
    was = checkout.recorded("sub1")
    _lock(
        checkout, {name: {"rev": checkout.recorded(name)} for name in ("sub1", "sub2")}
    )

    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    assert checkout.cli("land", "-m", "bump sub1") == 0
    assert checkout.recorded("sub1") == landed

    out = _status(checkout, capsys)
    assert f"lock-names-{was[:8]}" in out
    # sub2 did not move, so its two answers still agree.
    for line in out.splitlines():
        if line.startswith("sub2"):
            assert "lock-names" not in line
    assert "nix/sources.lock names a different commit" in out


def test_a_lock_ahead_of_the_pointer_is_reported_too(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """The row says which commit the lock names, not which side is wrong.

    A lock written from a newer revision runs ahead of the pointer, and that is
    the same disagreement seen from the other end.
    """
    checkout.edit("sub1", "v2")
    ahead = checkout.commit("sub1", "v2")
    _lock(checkout, {"sub1": {"rev": ahead}})

    assert f"lock-names-{ahead[:8]}" in _status(checkout, capsys)


def test_a_submodule_that_is_not_checked_out_still_gets_the_row(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pinned checkout has no submodule contents at all.

    That is the shape where the lock decides everything, so it is the shape
    where the row matters most. It needs no checkout to answer.
    """
    checkout = Checkout(lab.clone("pinned"))
    _lock(checkout, {"sub1": {"rev": "0" * 40}})

    out = _status(checkout, capsys)
    assert "not checked out" in out
    assert "lock-names-00000000" in out


# -- a lock nobody can read -------------------------------------------------


def test_an_unreadable_lock_stops_the_command(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = checkout.path / "nix"
    directory.mkdir(exist_ok=True)
    (directory / "sources.lock").write_text("{not json")

    assert checkout.cli("status") == 1
    assert "cannot be read" in capsys.readouterr().err


def test_a_lock_of_another_version_stops_the_command(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """Guessing at a format this tool does not know would report a drift that
    is not there, or hide one that is."""
    directory = checkout.path / "nix"
    directory.mkdir(exist_ok=True)
    (directory / "sources.lock").write_text(json.dumps({"version": 2, "sources": {}}))

    assert checkout.cli("status") == 1
    assert "version 2" in capsys.readouterr().err
