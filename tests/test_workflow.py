"""Landing and syncing, in both modes, including the hooks git runs."""

from __future__ import annotations

import subprocess

import pytest

from conftest import Checkout, Lab, needs_jj, node, run, write_lock

from umbrella import lock


def _git(checkout: Checkout, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=checkout.path, capture_output=True, text=True
    )


def _land_and_publish(checkout: Checkout, message: str) -> None:
    assert checkout.cli("land") == 0
    checkout.publish_umbrella(message)


# -- land ------------------------------------------------------------------


def test_land_publishes_the_commit_and_locks_it(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    assert checkout.locked("sub1") == landed
    on_origin = run("git", "rev-parse", "origin/main", cwd=checkout.at("sub1")).strip()
    assert on_origin == landed


def test_land_publishes_every_source_that_moved(checkout: Checkout) -> None:
    landed = {}
    for name in ("sub1", "sub2"):
        checkout.edit(name, "v2")
        landed[name] = checkout.commit(name, "v2")

    assert checkout.cli("land") == 0

    for name, oid in landed.items():
        assert checkout.locked(name) == oid


def test_land_leaves_alone_what_did_not_move(checkout: Checkout) -> None:
    before = checkout.locked("sub2")
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    assert checkout.locked("sub2") == before


def test_land_does_nothing_when_there_is_nothing_to_land(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checkout.cli("land") == 0
    assert "nothing to land" in capsys.readouterr().out


def test_land_refuses_to_move_anything_with_no_advance(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")

    assert checkout.cli("land", "--no-advance") == 1

    assert checkout.locked("sub1") != landed


def test_land_leaves_the_umbrella_commit_to_the_person(checkout: Checkout) -> None:
    """Two VCSs want two different commands, so land picks neither."""
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    assert lock.PATH in checkout.git("status", "--porcelain")


def test_land_from_a_detached_git_checkout_creates_the_branch(
    git_checkout: Checkout,
) -> None:
    """A working copy moved onto the locked revision is detached."""
    source = git_checkout.at("sub1")
    run("git", "checkout", "-q", "--detach", "HEAD", cwd=source)
    run("git", "branch", "-D", "main", cwd=source)
    git_checkout.edit("sub1", "v2")
    landed = git_checkout.commit_git("sub1", "v2")

    assert git_checkout.cli("land") == 0

    assert git_checkout.locked("sub1") == landed
    assert run("git", "rev-parse", "main", cwd=source).strip() == landed


# -- sync ------------------------------------------------------------------


def test_sync_moves_a_working_copy_onto_the_locked_revision(
    lab: Lab, checkout: Checkout
) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    landed = other.commit("sub1", "v2")
    _land_and_publish(other, "bump sub1")

    checkout.git("pull", "-q")
    assert checkout.cli("sync") == 0

    assert checkout.head_of("sub1") == landed


def test_sync_leaves_uncommitted_work_alone(lab: Lab, checkout: Checkout) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    _land_and_publish(other, "bump sub1")

    before = checkout.head_of("sub1")
    checkout.edit("sub1", "my work in progress")
    checkout.git("pull", "-q")

    assert checkout.cli("sync") == 0

    assert checkout.head_of("sub1") == before
    assert (checkout.at("sub1") / "file.txt").read_text() == "my work in progress\n"


def test_sync_is_quiet_when_everything_already_matches(checkout: Checkout) -> None:
    assert checkout.cli("sync") == 0
    assert checkout.cli("sync") == 0


def test_sync_steps_over_a_source_with_no_working_copy(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """Most sources have none, and that is not a fault to fail over."""
    checkout = Checkout(lab.clone("pinned"))
    assert checkout.cli("init") == 0

    assert checkout.cli("sync") == 0
    assert capsys.readouterr().err == ""


# -- status ----------------------------------------------------------------


def test_status_reports_a_remote_branch_that_moved_ahead(
    lab: Lab, checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    _land_and_publish(other, "bump sub1")

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


def test_status_reports_a_working_copy_behind_the_lock(
    lab: Lab, checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    _land_and_publish(other, "bump sub1")

    checkout.git("pull", "-q")

    assert checkout.cli("status", "--fetch") == 0
    assert "behind-lock" in capsys.readouterr().out


def test_status_will_not_guess_about_a_commit_it_has_not_fetched(
    lab: Lab, checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A source's clone is its own repo, and pulling the umbrella does not
    touch it. So right after a pull the locked commit is not here to compare
    against. Saying "behind" would be a guess."""
    other = checkout.peer(lab, "second")
    other.edit("sub1", "v2")
    other.commit("sub1", "v2")
    _land_and_publish(other, "bump sub1")

    checkout.git("pull", "-q")

    assert checkout.cli("status") == 0
    assert "locked-commit-not-in-checkout" in capsys.readouterr().out


# -- the hooks, run by git itself ------------------------------------------


def test_the_pre_commit_hook_blocks_a_private_revision(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.relock("sub1", checkout.commit("sub1", "v2"))
    checkout.git("add", "--", lock.PATH)

    done = _git(checkout, "commit", "-m", "bump sub1")

    assert done.returncode != 0
    assert "on no remote branch" in done.stderr


def test_the_pre_push_hook_blocks_a_private_revision(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.relock("sub1", checkout.commit("sub1", "v2"))
    checkout.git("add", "--", lock.PATH)
    checkout.git("commit", "-q", "--no-verify", "-m", "bump sub1")

    done = _git(checkout, "push", "origin", "main")

    assert done.returncode != 0
    assert "break every clone" in done.stderr


def test_the_pre_push_hook_allows_a_published_revision(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    checkout.relock("sub1", landed)
    checkout.git("add", "--", lock.PATH)
    checkout.git("commit", "-q", "--no-verify", "-m", "bump sub1")

    done = _git(checkout, "push", "origin", "main")

    assert done.returncode == 0, done.stderr


# -- the working commit itself --------------------------------------------


@needs_jj
def test_land_closes_a_described_working_commit(jj_checkout: Checkout) -> None:
    """edit then jj describe is a finished commit, even with no jj commit."""
    jj_checkout.edit("sub1", "v2")
    jj_checkout.jj("sub1", "describe", "-m", "v2")
    landed = jj_checkout.jj(
        "sub1", "log", "--no-graph", "-r", "@", "-T", "commit_id"
    ).strip()

    assert jj_checkout.cli("land") == 0

    assert jj_checkout.locked("sub1") == landed
    # The published commit must not still be the working copy, or the next
    # keystroke would rewrite something the remote already has.
    assert jj_checkout.head_of("sub1") == landed
    assert (
        jj_checkout.jj("sub1", "log", "--no-graph", "-r", "@", "-T", "empty").strip()
        == "true"
    )


@needs_jj
def test_land_leaves_an_undescribed_working_commit_alone(
    jj_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    before = jj_checkout.locked("sub1")
    jj_checkout.edit("sub1", "v2")

    assert jj_checkout.cli("land") == 0

    assert jj_checkout.locked("sub1") == before
    assert "no description" in capsys.readouterr().out


@needs_jj
def test_land_leaves_an_empty_described_working_commit_alone(
    jj_checkout: Checkout,
) -> None:
    before = jj_checkout.locked("sub1")
    jj_checkout.jj("sub1", "describe", "-m", "a message and nothing else")

    assert jj_checkout.cli("land") == 0

    assert jj_checkout.locked("sub1") == before


@needs_jj
def test_land_never_closes_a_conflicted_working_commit(
    jj_checkout: Checkout,
) -> None:
    """A described conflict is still a conflict, and must not reach the remote."""
    source = str(jj_checkout.at("sub1"))
    sides = {}
    for side in ("left", "right"):
        run("jj", "--no-pager", "-R", source, "new", "main")
        jj_checkout.edit("sub1", side)
        run("jj", "--no-pager", "-R", source, "commit", "-m", side)
        sides[side] = run(
            "jj",
            "--no-pager",
            "-R",
            source,
            "log",
            "--no-graph",
            "-r",
            "@-",
            "-T",
            "commit_id",
        ).strip()
    run("jj", "--no-pager", "-R", source, "new", sides["left"], sides["right"])
    run("jj", "--no-pager", "-R", source, "describe", "-m", "a described conflict")

    before = jj_checkout.locked("sub1")
    assert jj_checkout.cli("land") == 1
    assert jj_checkout.locked("sub1") == before


@needs_jj
def test_status_reports_work_hidden_in_another_workspace(
    jj_checkout: Checkout, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A jj workspace does not move git HEAD, so the umbrella cannot see it."""
    workspace = tmp_path / "side-workspace"
    jj_checkout.jj("sub1", "workspace", "add", str(workspace))
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
    jj_checkout.jj("sub1", "workspace", "add", str(workspace))

    assert jj_checkout.cli("status") == 0

    assert "cannot see" not in capsys.readouterr().out


def test_status_lines_up_when_a_name_is_long(
    lab: Lab, git_checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A source called tree-sitter-nix-numtide is wider than a fixed column."""
    long_name = "a-rather-long-source-name"
    entries = lock.entries(git_checkout.path)
    entries[long_name] = node(lab.url("sub1"), entries["sub1"]["rev"])
    write_lock(git_checkout.path, entries)
    assert git_checkout.cli("fetch", long_name) == 0

    assert git_checkout.cli("status") == 0

    lines = [
        row
        for row in capsys.readouterr().out.splitlines()
        if " in sync" in row or "HEAD" in row
    ]
    heads = {row.index("HEAD") for row in lines if "HEAD" in row}
    assert len(heads) == 1
    column = heads.pop()
    for line in lines:
        if "HEAD" not in line:
            # every row's third field starts in the same place
            assert line[column - 1] == " "
