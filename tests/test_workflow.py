"""Landing and syncing, in both modes, including the hooks git runs."""

from __future__ import annotations

import subprocess

import pytest

from conftest import Checkout, Lab, needs_jj, run


def _git(checkout: Checkout, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=checkout.path, capture_output=True, text=True
    )


# -- land ------------------------------------------------------------------


def test_land_publishes_the_commit_and_records_it(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")

    assert checkout.cli("land", "-m", "bump sub1") == 0

    assert checkout.recorded("sub1") == landed
    on_origin = run("git", "rev-parse", "origin/main", cwd=checkout.sub("sub1")).strip()
    assert on_origin == landed


def test_land_publishes_every_submodule_that_moved(checkout: Checkout) -> None:
    landed = {}
    for name in ("sub1", "sub2"):
        checkout.edit(name, "v2")
        landed[name] = checkout.commit(name, "v2")

    assert checkout.cli("land", "-m", "bump both") == 0

    for name, oid in landed.items():
        assert checkout.recorded(name) == oid


def test_land_leaves_alone_what_did_not_move(checkout: Checkout) -> None:
    before = checkout.recorded("sub2")
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land", "-m", "bump sub1") == 0

    assert checkout.recorded("sub2") == before


def test_land_does_nothing_when_there_is_nothing_to_land(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checkout.cli("land", "-m", "bump") == 0
    assert "nothing to land" in capsys.readouterr().out


def test_land_refuses_to_move_anything_with_no_advance(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")

    assert checkout.cli("land", "--no-advance", "-m", "bump") == 1

    assert checkout.recorded("sub1") != landed


def test_land_needs_a_message_to_push(checkout: Checkout) -> None:
    assert checkout.cli("land", "--push") == 1


def test_land_then_push_passes_both_hooks(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land", "-p", "-m", "bump sub1") == 0

    on_origin = run("git", "rev-parse", "origin/main", cwd=checkout.path).strip()
    assert on_origin == checkout.git("rev-parse", "HEAD").strip()


def test_land_from_a_detached_git_checkout_creates_the_branch(
    git_checkout: Checkout,
) -> None:
    """git detaches every submodule, so there may be no branch to advance."""
    sub = git_checkout.sub("sub1")
    run("git", "branch", "-D", "main", cwd=sub)
    git_checkout.edit("sub1", "v2")
    landed = git_checkout.commit_git("sub1", "v2")

    assert git_checkout.cli("land", "-m", "bump sub1") == 0

    assert git_checkout.recorded("sub1") == landed
    assert run("git", "rev-parse", "main", cwd=sub).strip() == landed


# -- sync ------------------------------------------------------------------


def test_sync_moves_a_submodule_onto_the_recorded_pointer(
    lab: Lab, checkout: Checkout
) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    landed = other.commit("sub1", "v2")
    assert other.cli("land", "-p", "-m", "bump sub1") == 0

    checkout.git("pull", "-q")
    assert checkout.cli("sync") == 0

    assert checkout.head_of("sub1") == landed


def test_sync_leaves_uncommitted_work_alone(lab: Lab, checkout: Checkout) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    assert other.cli("land", "-p", "-m", "bump sub1") == 0

    before = checkout.head_of("sub1")
    checkout.edit("sub1", "my work in progress")
    checkout.git("pull", "-q")

    assert checkout.cli("sync") == 0

    assert checkout.head_of("sub1") == before
    assert (checkout.sub("sub1") / "file.txt").read_text() == "my work in progress\n"


def test_sync_is_quiet_when_everything_already_matches(checkout: Checkout) -> None:
    assert checkout.cli("sync") == 0
    assert checkout.cli("sync") == 0


# -- status ----------------------------------------------------------------


def test_status_reports_a_remote_branch_that_moved_ahead(
    lab: Lab, checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    assert other.cli("land", "-p", "-m", "bump sub1") == 0

    # Without a fetch this checkout cannot know, and status must not pretend.
    assert checkout.cli("status") == 0
    assert "moved-ahead" not in capsys.readouterr().out

    assert checkout.cli("status", "--fetch") == 0
    assert "origin/main-moved-ahead" in capsys.readouterr().out


def test_status_reports_an_unpushed_commit(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("status") == 0

    assert "not-pushed" in capsys.readouterr().out


def test_status_reports_a_submodule_behind_the_umbrella(
    lab: Lab, checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    assert other.cli("land", "-p", "-m", "bump sub1") == 0

    checkout.git("pull", "-q")

    assert checkout.cli("status", "--fetch") == 0
    assert "behind-umbrella" in capsys.readouterr().out


@needs_jj
def test_status_will_not_guess_about_a_commit_it_has_not_fetched(
    lab: Lab, jj_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """jj mode sets submodule.recurse false, to keep git checkout away from the
    jj working copies. That also stops git fetching submodule commits on demand,
    so after a plain pull the recorded commit is not here to compare against.
    Saying "behind" would be a guess, so status says it does not have it.
    """
    other = jj_checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    assert other.cli("land", "-p", "-m", "bump sub1") == 0

    jj_checkout.git("pull", "-q")

    assert jj_checkout.cli("status") == 0
    assert "pointer-not-in-checkout" in capsys.readouterr().out


# -- the hooks, run by git itself ------------------------------------------


def test_the_pre_commit_hook_blocks_a_private_pointer(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    checkout.git("add", "--", "sub1")

    done = _git(checkout, "commit", "-m", "bump sub1")

    assert done.returncode != 0
    assert "on no remote branch" in done.stderr


def test_the_pre_push_hook_blocks_a_private_pointer(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    checkout.git("add", "--", "sub1")
    checkout.git("commit", "-q", "--no-verify", "-m", "bump sub1")

    done = _git(checkout, "push", "origin", "main")

    assert done.returncode != 0
    assert "break every clone" in done.stderr


def test_the_pre_push_hook_allows_a_published_pointer(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    checkout.git("add", "--", "sub1")
    checkout.git("commit", "-q", "--no-verify", "-m", "bump sub1")

    done = _git(checkout, "push", "origin", "main")

    assert done.returncode == 0, done.stderr


# -- the working commit itself --------------------------------------------


@needs_jj
def test_land_closes_a_described_working_commit(jj_checkout: Checkout) -> None:
    """edit then jj describe is a finished commit, even with no jj commit."""
    jj_checkout.edit("sub1", "v2")
    jj_checkout.jj("sub1", "describe", "-m", "v2")
    landed = run(
        "jj", "--no-pager", "-R", str(jj_checkout.sub("sub1")),
        "log", "--no-graph", "-r", "@", "-T", "commit_id",
    ).strip()

    assert jj_checkout.cli("land", "-m", "bump sub1") == 0

    assert jj_checkout.recorded("sub1") == landed
    # The published commit must not still be the working copy, or the next
    # keystroke would rewrite something the remote already has.
    assert jj_checkout.head_of("sub1") == landed
    assert (
        run(
            "jj", "--no-pager", "-R", str(jj_checkout.sub("sub1")),
            "log", "--no-graph", "-r", "@", "-T", "empty",
        ).strip()
        == "true"
    )


@needs_jj
def test_land_leaves_an_undescribed_working_commit_alone(
    jj_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    before = jj_checkout.recorded("sub1")
    jj_checkout.edit("sub1", "v2")

    assert jj_checkout.cli("land", "-m", "bump sub1") == 0

    assert jj_checkout.recorded("sub1") == before
    assert "no description" in capsys.readouterr().out


@needs_jj
def test_land_leaves_an_empty_described_working_commit_alone(
    jj_checkout: Checkout,
) -> None:
    before = jj_checkout.recorded("sub1")
    jj_checkout.jj("sub1", "describe", "-m", "a message and nothing else")

    assert jj_checkout.cli("land", "-m", "bump sub1") == 0

    assert jj_checkout.recorded("sub1") == before


@needs_jj
def test_land_never_closes_a_conflicted_working_commit(
    jj_checkout: Checkout,
) -> None:
    """A described conflict is still a conflict, and must not reach the remote."""
    sub = str(jj_checkout.sub("sub1"))
    sides = {}
    for side in ("left", "right"):
        run("jj", "--no-pager", "-R", sub, "new", "main")
        jj_checkout.edit("sub1", side)
        run("jj", "--no-pager", "-R", sub, "commit", "-m", side)
        sides[side] = run(
            "jj", "--no-pager", "-R", sub, "log", "--no-graph", "-r", "@-",
            "-T", "commit_id",
        ).strip()
    run("jj", "--no-pager", "-R", sub, "new", sides["left"], sides["right"])
    run("jj", "--no-pager", "-R", sub, "describe", "-m", "a described conflict")

    before = jj_checkout.recorded("sub1")
    assert jj_checkout.cli("land", "-m", "bump sub1") == 1
    assert jj_checkout.recorded("sub1") == before


@needs_jj
def test_status_reports_work_hidden_in_another_workspace(
    jj_checkout: Checkout, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A jj workspace does not move git HEAD, so the umbrella cannot see it."""
    workspace = tmp_path / "side-workspace"
    run(
        "jj", "--no-pager", "-R", str(jj_checkout.sub("sub1")),
        "workspace", "add", str(workspace),
    )
    (workspace / "file.txt").write_text("work done elsewhere\n")
    run("jj", "--no-pager", "-R", str(workspace), "commit", "-m", "elsewhere")

    assert jj_checkout.cli("status") == 0

    out = capsys.readouterr().out
    assert "side-workspace holds work this checkout cannot see" in out


@needs_jj
def test_status_says_nothing_about_a_workspace_with_no_unseen_work(
    jj_checkout: Checkout, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "quiet-workspace"
    run(
        "jj", "--no-pager", "-R", str(jj_checkout.sub("sub1")),
        "workspace", "add", str(workspace),
    )

    assert jj_checkout.cli("status") == 0

    assert "cannot see" not in capsys.readouterr().out




def test_status_lines_up_when_a_name_is_long(
    lab: Lab, git_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A submodule called prompt-toolkit is wider than the old fixed column."""
    long_name = "a-rather-long-submodule-name"
    run(
        "git", "submodule", "add", "-q", str(lab.origin("sub1")), long_name,
        cwd=git_checkout.path,
    )
    run("git", "commit", "-qm", "add a long name", cwd=git_checkout.path)

    assert git_checkout.cli("status") == 0

    lines = [l for l in capsys.readouterr().out.splitlines() if " in sync" in l or "HEAD" in l]
    heads = {l.index("HEAD") for l in lines if "HEAD" in l}
    assert len(heads) == 1
    column = heads.pop()
    for line in lines:
        if "HEAD" not in line:
            # every row's second field starts in the same place
            assert line[column - 1] == " "
