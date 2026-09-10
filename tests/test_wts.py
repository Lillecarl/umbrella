"""Worktreespaces: a throwaway checkout of the whole constellation."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import Checkout, needs_jj, run


def _wts_path(checkout: Checkout, name: str) -> Path:
    return checkout.path.parent / f"{checkout.path.name}-{name}"


def test_a_worktree_is_a_working_constellation(checkout: Checkout) -> None:
    assert checkout.cli("wts", "add", "poc") == 0
    tree = _wts_path(checkout, "poc")

    for name in ("sub1", "sub2"):
        assert (tree / name / "file.txt").read_text() == f"{name} v1\n"
        assert (tree / name / "file.txt").read_text() == (
            checkout.at(name) / "file.txt"
        ).read_text()


def test_a_worktree_is_listed(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checkout.cli("wts", "add", "poc") == 0
    capsys.readouterr()

    assert checkout.cli("wts", "list") == 0

    out = capsys.readouterr().out
    assert "poc" in out and str(_wts_path(checkout, "poc")) in out


def test_an_umbrella_worktree_does_not_publish(checkout: Checkout) -> None:
    assert checkout.cli("wts", "add", "poc") == 0
    inside = Checkout(_wts_path(checkout, "poc"))

    assert inside.cli("land") == 1


def test_a_worktree_cannot_spawn_another(checkout: Checkout) -> None:
    assert checkout.cli("wts", "add", "poc") == 0
    inside = Checkout(_wts_path(checkout, "poc"))

    assert inside.cli("wts", "add", "deeper") == 1


def test_removing_a_worktree_takes_the_whole_tree(checkout: Checkout) -> None:
    assert checkout.cli("wts", "add", "poc") == 0
    tree = _wts_path(checkout, "poc")
    assert tree.exists()

    assert checkout.cli("wts", "rm", "poc") == 0

    assert not tree.exists()


def test_a_worktree_refuses_a_path_that_is_taken(checkout: Checkout) -> None:
    _wts_path(checkout, "poc").mkdir()

    assert checkout.cli("wts", "add", "poc") == 1


@needs_jj
def test_a_jj_worktree_shares_the_source_repo(jj_checkout: Checkout) -> None:
    """It is a workspace, not a second clone. That is what makes it cheap."""
    assert jj_checkout.cli("wts", "add", "poc") == 0

    listed = run(
        "jj", "--no-pager", "-R", str(jj_checkout.at("sub1")), "workspace", "list"
    )
    assert "poc:" in listed


@needs_jj
def test_work_in_a_worktree_shows_up_in_the_source_status(
    jj_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert jj_checkout.cli("wts", "add", "poc") == 0
    tree = _wts_path(jj_checkout, "poc")
    (tree / "sub1" / "file.txt").write_text("work in the wts\n")
    run("jj", "--no-pager", "-R", str(tree / "sub1"), "commit", "-m", "work in the wts")
    capsys.readouterr()

    assert jj_checkout.cli("status") == 0

    assert (
        "workspace poc holds work this checkout cannot see" in capsys.readouterr().out
    )


def test_a_wts_can_be_made_from_an_older_umbrella_commit(checkout: Checkout) -> None:
    """The revisions come from that commit's lock, not from the one on disk."""
    was = checkout.locked("sub1")
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    assert checkout.cli("land") == 0
    checkout.publish_umbrella("bump sub1")
    assert checkout.locked("sub1") != was

    assert checkout.cli("wts", "add", "before", "-r", "HEAD~1") == 0

    # Asserted through the files, because a jj workspace has no git of its own
    # and git rev-parse inside one resolves to the umbrella above it.
    tree = _wts_path(checkout, "before")
    assert (tree / "sub1" / "file.txt").read_text() == "sub1 v1\n"
    assert (checkout.at("sub1") / "file.txt").read_text() == "v2\n"


def test_a_wts_from_a_revision_that_does_not_exist_is_refused(
    checkout: Checkout,
) -> None:
    assert checkout.cli("wts", "add", "nope", "-r", "no-such-revision") == 1


def test_an_umbrella_wts_is_clean_in_git_status(checkout: Checkout) -> None:
    """The generated hooks must be excluded there too.

    info/exclude is shared across worktrees and lives in the common git
    directory. Writing it into a linked worktree's own git directory has no
    effect at all, because git never reads it there.
    """
    assert checkout.cli("wts", "add", "poc") == 0

    tree = _wts_path(checkout, "poc")
    assert run("git", "status", "--porcelain", cwd=tree) == ""


def test_a_jj_workspace_counts_as_a_working_copy(jj_checkout: Checkout) -> None:
    """It has no .git of its own, so .git alone is the wrong question."""
    assert jj_checkout.cli("wts", "add", "poc") == 0
    tree = _wts_path(jj_checkout, "poc")

    inside = Checkout(tree).umbrella()
    for source in inside.sources():
        assert not (source.workdir / ".git").exists()
        assert source.present


def test_status_in_a_worktreespace_says_what_it_is(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """It must not claim the working copies are missing, nor advise a fetch.

    Following that advice would clone over live jj workspaces.
    """
    assert checkout.cli("wts", "add", "poc") == 0
    capsys.readouterr()

    assert Checkout(_wts_path(checkout, "poc")).cli("status") == 0

    out = capsys.readouterr().out
    assert "worktreespace poc" in out
    assert "no working copy" not in out
    assert "umbrella fetch" not in out


def test_sync_in_a_worktreespace_refuses(checkout: Checkout) -> None:
    assert checkout.cli("wts", "add", "poc") == 0

    assert Checkout(_wts_path(checkout, "poc")).cli("sync") == 1
