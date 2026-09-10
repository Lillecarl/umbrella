"""Getting a checkout into a working state."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import Checkout, Lab, needs_jj, run

from umbrella import ignore, mode
from umbrella.kind import Kind
from umbrella.model import Umbrella


def test_a_fresh_clone_has_no_working_copies(lab: Lab) -> None:
    """The default. Every source resolves from the lock and costs nothing."""
    checkout = Checkout(lab.clone())
    for name in ("sub1", "sub2"):
        assert not checkout.at(name).exists()
    assert [s.name for s in checkout.umbrella().sources()] == ["sub1", "sub2"]


def test_status_does_not_mistake_the_umbrella_for_a_working_copy(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty source directory must not resolve upward to the umbrella."""
    checkout = Checkout(lab.clone())
    for source in checkout.umbrella().sources():
        assert not source.present

    assert checkout.cli("status") == 0
    assert "with no working copy here" in capsys.readouterr().out


def test_fetch_clones_at_the_locked_revision(lab: Lab) -> None:
    checkout = Checkout(lab.clone())
    assert checkout.cli("init") == 0
    assert checkout.cli("fetch", "sub1") == 0

    assert checkout.head_of("sub1") == checkout.locked("sub1")
    assert not checkout.at("sub2").exists()


def test_fetch_all_takes_every_source(lab: Lab) -> None:
    checkout = Checkout(lab.clone())
    assert checkout.cli("init") == 0
    assert checkout.cli("fetch", "--all") == 0

    for name in ("sub1", "sub2"):
        assert (checkout.at(name) / "file.txt").read_text() == f"{name} v1\n"


def test_fetch_refuses_a_name_the_lock_does_not_have(
    git_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert git_checkout.cli("fetch", "nixpkgs") == 1
    assert "names no nixpkgs" in capsys.readouterr().err


def test_a_working_copy_is_ignored_by_the_umbrella(git_checkout: Checkout) -> None:
    """The whole reason a source can be an ordinary clone here.

    Nothing records it, so nothing must add it either. One `git add .` in the
    umbrella would otherwise commit another project's tree.
    """
    text = (git_checkout.path / ignore.FILE).read_text()
    assert "/sub1/" in text and "/sub2/" in text

    git_checkout.git("add", ".")
    staged = git_checkout.git("diff", "--cached", "--name-only")
    assert "sub1/" not in staged


def test_init_is_git_mode_by_default(git_checkout: Checkout) -> None:
    assert mode.read(git_checkout.umbrella().repo) is mode.Mode.GIT
    assert not (git_checkout.at("sub1") / ".jj").exists()


@needs_jj
def test_init_jj_colocates_every_working_copy(jj_checkout: Checkout) -> None:
    assert mode.read(jj_checkout.umbrella().repo) is mode.Mode.JJ
    for name in ("sub1", "sub2"):
        assert (jj_checkout.at(name) / ".jj").is_dir()


@needs_jj
def test_the_mode_marker_is_never_committed(jj_checkout: Checkout) -> None:
    assert (Path(jj_checkout.umbrella().repo.path) / mode.MARKER).exists()
    assert "umbrella-mode" not in jj_checkout.git("status", "--porcelain")


@needs_jj
def test_init_twice_does_not_move_an_existing_checkout(jj_checkout: Checkout) -> None:
    jj_checkout.edit("sub1", "work in progress")
    landed = jj_checkout.commit_jj("sub1", "wip")

    assert jj_checkout.cli("initjj") == 0

    assert jj_checkout.head_of("sub1") == landed


@needs_jj
def test_a_jj_umbrella_is_allowed(git_checkout: Checkout) -> None:
    """The point of dropping the submodules.

    A gitlink is the one thing jj cannot record, and there is none any more.
    An umbrella is now a text file, which jj handles like any other.
    """
    from umbrella import jj

    jj.init_colocate(git_checkout.path)

    assert git_checkout.umbrella().kind is Kind.UMBRELLA
    assert git_checkout.cli("status") == 0


def test_a_repo_without_a_lock_is_a_single_project(tmp_path: Path) -> None:
    """No nix/sources.lock is not an error. It is the common shape."""
    run("git", "init", "-q", "-b", "main", str(tmp_path / "plain"))
    previous = Path.cwd()
    os.chdir(tmp_path / "plain")
    try:
        assert Umbrella.open().kind is Kind.SINGLE
    finally:
        os.chdir(previous)


def test_a_repo_with_a_lock_is_an_umbrella(git_checkout: Checkout) -> None:
    assert git_checkout.umbrella().kind is Kind.UMBRELLA


def test_the_kind_marker_overrides_detection(git_checkout: Checkout) -> None:
    """A lock that is a build artefact is not a constellation of projects."""
    assert git_checkout.cli("kind", "single") == 0

    umbrella = git_checkout.umbrella()
    assert umbrella.kind is Kind.SINGLE
    assert umbrella.sources() == []


def test_init_does_not_clobber_an_existing_hooks_path(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """Someone else's hooks matter more than ours."""
    checkout = Checkout(lab.clone())
    checkout.git("config", "core.hooksPath", ".their-hooks")

    assert checkout.cli("init") == 0

    out = capsys.readouterr().out
    assert "left alone" in out
    assert checkout.git("config", "core.hooksPath").strip() == ".their-hooks"
    assert not (checkout.path / ".githooks").exists()


# -- the shape every checkout made before this change is in -----------------


def _as_a_submodule(path: Path, lab: Lab, name: str) -> None:
    """Add a source the way the old tool did: a gitlink and a module gitdir."""
    run(
        "git",
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        "--force",
        str(lab.origin(name)),
        name,
        cwd=path,
    )


def test_init_moves_a_repository_out_of_git_modules(lab: Lab, tmp_path: Path) -> None:
    """A submodule keeps its history inside the umbrella's .git.

    Leaving it there once the gitlinks are gone is quiet and bad: the clones
    keep working, so nobody notices, and one `rm -rf .git` takes every one of
    them.
    """
    path = tmp_path / "old-shape"
    run("git", "clone", "-q", str(lab.origin("umbrella")), str(path))
    _as_a_submodule(path, lab, "sub1")
    assert (path / "sub1" / ".git").is_file()

    assert Checkout(path).cli("init") == 0

    assert (path / "sub1" / ".git").is_dir()
    assert run("git", "status", "--porcelain", cwd=path / "sub1") == ""
    assert not (path / ".git" / "modules" / "sub1").exists()


@needs_jj
def test_adopting_a_repository_keeps_a_colocated_jj_working(
    lab: Lab, tmp_path: Path
) -> None:
    """The operation log is the only copy of work that is not committed yet.

    A colocated `.jj/repo/store/git_target` names `../../../.git`, which is the
    same location before and after: a pointer file first, a real directory
    second. This asserts it, because the migration depends on it.
    """
    from umbrella import jj

    path = tmp_path / "old-shape-jj"
    run("git", "clone", "-q", str(lab.origin("umbrella")), str(path))
    _as_a_submodule(path, lab, "sub1")
    jj.init_colocate(path / "sub1")
    run("jj", "--no-pager", "-R", str(path / "sub1"), "describe", "-m", "before")

    assert Checkout(path).cli("init", "--jj") == 0

    assert (path / "sub1" / ".git").is_dir()
    described = run(
        "jj",
        "--no-pager",
        "-R",
        str(path / "sub1"),
        "log",
        "--no-graph",
        "-r",
        "@",
        "-T",
        "description",
    )
    assert described.strip() == "before"


def test_init_is_safe_to_run_again_on_an_ordinary_clone(
    git_checkout: Checkout,
) -> None:
    assert git_checkout.cli("init") == 0
    assert (git_checkout.at("sub1") / ".git").is_dir()
