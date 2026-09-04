"""The checks that keep a private submodule commit out of public history.

These run in both modes. The guards ask git questions only, so behaving the
same under git and jj is the claim, and it is asserted rather than assumed.
"""

from __future__ import annotations

from conftest import ZERO, Checkout, push_line, run

from umbrella import guards


def _record(checkout: Checkout, sub: str, message: str) -> None:
    """Stage and commit a pointer, going around the pre-commit hook."""
    checkout.git("add", "--", sub)
    checkout.git("commit", "-q", "--no-verify", "-m", message)


def _check_push(checkout: Checkout) -> list[guards.Problem]:
    tip = checkout.git("rev-parse", "HEAD").strip()
    remote = checkout.git("rev-parse", "origin/main").strip()
    return guards.check_push(
        checkout.umbrella(), checkout.backend(), "origin", push_line(tip, remote)
    )


# -- pre-commit ------------------------------------------------------------


def test_check_commit_blocks_a_pointer_on_no_remote_branch(
    checkout: Checkout,
) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    checkout.git("add", "--", "sub1")

    problems = guards.check_commit(checkout.umbrella())

    assert [p.path for p in problems] == ["sub1"]
    assert str(problems[0].oid) == landed


def test_check_commit_passes_once_the_commit_is_pushed(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    checkout.git("add", "--", "sub1")

    assert guards.check_commit(checkout.umbrella()) == []


def test_check_commit_ignores_a_pointer_that_did_not_change(
    checkout: Checkout,
) -> None:
    assert guards.check_commit(checkout.umbrella()) == []


def test_check_commit_names_every_offending_submodule(checkout: Checkout) -> None:
    for name in ("sub1", "sub2"):
        checkout.edit(name, "v2")
        checkout.commit(name, "v2")
        checkout.git("add", "--", name)

    problems = guards.check_commit(checkout.umbrella())

    assert sorted(p.path for p in problems) == ["sub1", "sub2"]


# -- pre-push --------------------------------------------------------------


def test_check_push_blocks_a_private_pointer(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    _record(checkout, "sub1", "bump sub1")

    assert [p.path for p in _check_push(checkout)] == ["sub1"]


def test_check_push_passes_when_everything_is_public(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    _record(checkout, "sub1", "bump sub1")

    assert _check_push(checkout) == []


def test_check_push_passes_on_a_range_with_no_pointer_changes(
    checkout: Checkout,
) -> None:
    (checkout.path / "notes.txt").write_text("nothing to do with submodules\n")
    checkout.git("add", "notes.txt")
    checkout.git("commit", "-q", "-m", "unrelated")

    assert _check_push(checkout) == []


def test_check_push_reads_every_commit_in_the_range(checkout: Checkout) -> None:
    """A clone can check out an intermediate commit, so the tip is not enough."""
    checkout.edit("sub1", "orphan")
    orphan = checkout.commit("sub1", "orphan")
    _record(checkout, "sub1", "records an orphan")

    # A second line that does reach the remote, recorded on top.
    sub = checkout.sub("sub1")
    if checkout.mode == "jj":
        checkout.jj("sub1", "new", "main")
    else:
        run("git", "checkout", "-q", "-B", "main", "origin/main", cwd=sub)
    checkout.edit("sub1", "public")
    checkout.commit("sub1", "public")
    checkout.publish("sub1")
    _record(checkout, "sub1", "records a public commit")

    # The tip is fine. The commit under it is not, and that is what must be caught.
    assert [str(p.oid) for p in _check_push(checkout)] == [orphan]


def test_check_push_fetches_so_a_stale_ref_does_not_block(
    checkout: Checkout,
) -> None:
    sub = checkout.sub("sub1")
    base = run("git", "rev-parse", "origin/main", cwd=sub).strip()

    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    _record(checkout, "sub1", "bump sub1")

    # Rewind what this checkout believes about the remote.
    run("git", "update-ref", "refs/remotes/origin/main", base, cwd=sub)

    assert _check_push(checkout) == []


def test_a_new_branch_push_is_measured_against_the_remote(
    checkout: Checkout,
) -> None:
    problems = guards.check_push(
        checkout.umbrella(),
        checkout.backend(),
        "origin",
        push_line(checkout.git("rev-parse", "HEAD").strip(), ZERO),
    )
    assert problems == []


def test_a_branch_deletion_carries_no_pointer(checkout: Checkout) -> None:
    problems = guards.check_push(
        checkout.umbrella(),
        checkout.backend(),
        "origin",
        push_line(ZERO, checkout.git("rev-parse", "HEAD").strip()),
    )
    assert problems == []


def test_an_unreadable_push_line_is_ignored(checkout: Checkout) -> None:
    """git writes one line per ref. Anything else is not ours to interpret."""
    problems = guards.check_push(
        checkout.umbrella(), checkout.backend(), "origin", "garbage\n\n"
    )
    assert problems == []
