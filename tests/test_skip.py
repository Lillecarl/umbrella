"""Leaving a submodule out of a checkout.

A source with no working copy resolves from the lock, one source at a time.
That is a fact about the Nix side and it already worked. These are about the
tool: it used to check every submodule out again, fail over a missing one, and
call it a fault in `status`.
"""

from __future__ import annotations

import pytest

from conftest import Checkout, Lab, needs_jj, run
from umbrella import skip


def fresh(lab: Lab, name: str = "picky") -> Checkout:
    """A clone with no submodule contents, and no init yet."""
    return Checkout(lab.clone(name))


def out(checkout: Checkout, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert checkout.cli(*args) == 0
    return capsys.readouterr().out


# -- saying so --------------------------------------------------------------


def test_skip_then_init_checks_out_only_the_rest(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fresh(lab)

    assert checkout.cli("skip", "sub2") == 0
    capsys.readouterr()
    assert checkout.cli("initgit") == 0

    assert (checkout.sub("sub1") / ".git").exists()
    assert not (checkout.sub("sub2") / ".git").exists()
    assert "left to the lock" in capsys.readouterr().out


def test_skip_with_no_arguments_lists_what_is_left_out(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fresh(lab)
    assert "nothing is skipped" in out(checkout, capsys, "skip")

    assert checkout.cli("skip", "sub2") == 0
    capsys.readouterr()

    listed = out(checkout, capsys, "skip")
    assert "sub2" in listed
    assert "sub1" not in listed


def test_skip_refuses_a_path_that_is_not_a_submodule(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fresh(lab)

    assert checkout.cli("skip", "nosuch") == 1

    assert "not a submodule" in capsys.readouterr().err


def test_skip_refuses_a_submodule_that_is_checked_out(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nix reads a working copy before the lock, so this would change nothing.

    Recording it quietly would leave a checkout whose tool and whose build
    disagree about where a source comes from.
    """
    assert checkout.cli("skip", "sub2") == 1

    assert "Remove the directory contents first" in capsys.readouterr().err
    assert skip.read(checkout.umbrella().repo) == set()


def test_rm_stops_skipping(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    capsys.readouterr()

    assert checkout.cli("skip", "--rm", "sub2") == 0

    assert "no longer skipped" in capsys.readouterr().out
    assert skip.read(checkout.umbrella().repo) == set()
    assert checkout.cli("initgit") == 0
    assert (checkout.sub("sub2") / ".git").exists()


def test_the_choice_is_never_committed(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """It is one person's opinion about one checkout, like the mode."""
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    capsys.readouterr()

    assert checkout.git("status", "--porcelain").strip() == ""
    assert (checkout.path / ".git" / skip.MARKER).is_file()


# -- what the other commands do --------------------------------------------


def test_status_calls_it_the_lock_and_not_a_mistake(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    assert checkout.cli("initgit") == 0
    capsys.readouterr()

    shown = out(checkout, capsys, "status")

    row = next(line for line in shown.splitlines() if line.startswith("sub2"))
    assert "from the lock" in row
    assert "not checked out" not in row


def test_status_says_when_a_skipped_submodule_is_checked_out_anyway(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker is about the tool. Nix still reads the directory."""
    skip.write(checkout.umbrella().repo, {"sub2"})

    shown = out(checkout, capsys, "status")

    row = next(line for line in shown.splitlines() if line.startswith("sub2"))
    assert "skipped-but-checked-out" in row


def test_sync_steps_over_it(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    assert checkout.cli("initgit") == 0
    capsys.readouterr()

    assert "left to the lock" in out(checkout, capsys, "sync")


def test_land_still_publishes_everything_else(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one that would otherwise make skipping useless.

    land used to refuse over any submodule that was not checked out. Leaving
    one out would then have made every other one unlandable.
    """
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    assert checkout.cli("initgit") == 0
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    capsys.readouterr()

    assert checkout.cli("land", "-m", "bump sub1") == 0

    assert checkout.recorded("sub1") == landed


def test_a_commit_by_hand_does_not_crash_the_pre_commit_hook(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """libgit2 refuses to open an empty directory as a repository.

    The guard used to ask whether the directory was there, which it is after a
    clone with no --recurse-submodules. Skipping makes that the normal state.
    """
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    assert checkout.cli("initgit") == 0
    capsys.readouterr()

    assert checkout.cli("check-commit") == 0


@needs_jj
def test_initjj_leaves_a_skipped_submodule_alone(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fresh(lab, "jj-picky")
    assert checkout.cli("skip", "sub2") == 0

    assert checkout.cli("initjj") == 0

    assert (checkout.sub("sub1") / ".jj").is_dir()
    assert not (checkout.sub("sub2") / ".jj").exists()
    capsys.readouterr()
    assert checkout.cli("mode", "jj") == 0


def test_a_worktreespace_inherits_the_choice(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    assert checkout.cli("initgit") == 0
    capsys.readouterr()

    assert checkout.cli("wts", "add", "spare") == 0
    made = checkout.path.parent / f"{checkout.path.name}-spare"

    assert (made / "sub1").is_dir()
    assert not (made / "sub2" / ".git").exists()
    assert skip.read(Checkout(made).umbrella().repo) == {"sub2"}

    assert checkout.cli("wts", "rm", "spare") == 0


def test_pushing_a_pointer_for_a_skipped_submodule_is_still_refused(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guarantee does not bend for this.

    The pre-push hook verifies a pointer by looking in the checkout. A skipped
    submodule has none, so it says it cannot verify rather than letting the
    pointer through.
    """
    from conftest import push_line

    checkout = fresh(lab)
    assert checkout.cli("skip", "sub2") == 0
    assert checkout.cli("initgit") == 0

    moved = lab.push_from_elsewhere("sub2", "v2")
    run("git", "update-index", "--add", "--cacheinfo", f"160000,{moved},sub2",
        cwd=checkout.path)
    run("git", "commit", "-qm", "point at sub2", cwd=checkout.path)
    tip = run("git", "rev-parse", "HEAD", cwd=checkout.path).strip()
    capsys.readouterr()

    assert checkout.cli_stdin(push_line(tip), "check-push", "origin") == 1

    assert "cannot be verified" in capsys.readouterr().err
