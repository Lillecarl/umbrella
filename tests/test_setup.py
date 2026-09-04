"""Getting a checkout into a working state."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import Checkout, Lab, needs_jj

from umbrella import mode
from umbrella.model import Umbrella, UmbrellaError


def test_initgit_resolves_a_clone_made_without_recurse_submodules(lab: Lab) -> None:
    checkout = Checkout(lab.clone())
    assert not (checkout.sub("sub1") / ".git").exists()

    assert checkout.cli("initgit") == 0

    for name in ("sub1", "sub2"):
        assert (checkout.sub(name) / "file.txt").read_text() == f"{name} v1\n"


def test_status_does_not_mistake_the_umbrella_for_a_missing_submodule(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty submodule directory must not resolve upward to the umbrella."""
    checkout = Checkout(lab.clone())
    umbrella = checkout.umbrella()
    for sub in umbrella.subs():
        assert not sub.present

    assert checkout.cli("status") == 0
    assert "not checked out" in capsys.readouterr().out


def test_init_is_git_mode_by_default(git_checkout: Checkout) -> None:
    assert mode.read(git_checkout.umbrella().repo) is mode.Mode.GIT
    assert not (git_checkout.sub("sub1") / ".jj").exists()


@needs_jj
def test_initjj_colocates_every_submodule(jj_checkout: Checkout) -> None:
    assert mode.read(jj_checkout.umbrella().repo) is mode.Mode.JJ
    for name in ("sub1", "sub2"):
        assert (jj_checkout.sub(name) / ".jj").is_dir()


@needs_jj
def test_the_mode_marker_is_never_committed(jj_checkout: Checkout) -> None:
    assert (Path(jj_checkout.umbrella().repo.path) / mode.MARKER).exists()
    assert jj_checkout.git("status", "--porcelain") == ""


@needs_jj
def test_initjj_twice_does_not_move_an_existing_checkout(jj_checkout: Checkout) -> None:
    """The regression: init used to run git submodule update over a jj working copy."""
    jj_checkout.edit("sub1", "work in progress")
    landed = jj_checkout.commit_jj("sub1", "wip")

    assert jj_checkout.cli("initjj") == 0

    assert jj_checkout.head_of("sub1") == landed


def test_a_jj_umbrella_is_refused(git_checkout: Checkout) -> None:
    (git_checkout.path / ".jj").mkdir()
    with pytest.raises(UmbrellaError, match="never record a submodule pointer"):
        git_checkout.umbrella()


def test_a_repo_without_submodules_is_a_single_project(tmp_path: Path) -> None:
    """No .gitmodules is not an error. It is the common shape."""
    import os

    from conftest import run

    from umbrella.kind import Kind

    run("git", "init", "-q", "-b", "main", str(tmp_path / "plain"))
    previous = Path.cwd()
    os.chdir(tmp_path / "plain")
    try:
        assert Umbrella.open().kind is Kind.SINGLE
    finally:
        os.chdir(previous)


def test_an_umbrella_with_submodules_is_detected(git_checkout: Checkout) -> None:
    from umbrella.kind import Kind

    assert git_checkout.umbrella().kind is Kind.UMBRELLA


def test_the_kind_marker_overrides_detection(git_checkout: Checkout) -> None:
    """Submodules that are vendored dependencies are not a constellation."""
    from umbrella.kind import Kind

    assert git_checkout.cli("kind", "single") == 0

    umbrella = git_checkout.umbrella()
    assert umbrella.kind is Kind.SINGLE
    assert umbrella.subs() == []


def test_init_does_not_clobber_an_existing_hooks_path(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """Someone else's hooks matter more than ours."""
    checkout = Checkout(lab.clone())
    checkout.git("config", "core.hooksPath", ".their-hooks")

    assert checkout.cli("initgit") == 0

    out = capsys.readouterr().out
    assert "left alone" in out
    assert checkout.git("config", "core.hooksPath").strip() == ".their-hooks"
    assert not (checkout.path / ".githooks").exists()
