"""The checks that keep a private commit out of a public lock.

These run in both modes. The guards ask git questions only, so behaving the
same under git and jj is the claim, and it is asserted rather than assumed.
"""

from __future__ import annotations

from conftest import ZERO, Checkout, push_line, run

from umbrella import guards, lock


def _stage(checkout: Checkout, name: str, rev: str) -> None:
    """Put a revision in the lock and stage it, as `land` then a commit would."""
    checkout.relock(name, rev)
    checkout.git("add", "--", lock.PATH)


def _record(checkout: Checkout, name: str, rev: str, message: str) -> None:
    """Stage and commit a revision, going around the pre-commit hook."""
    _stage(checkout, name, rev)
    checkout.git("commit", "-q", "--no-verify", "-m", message)


def _check_push(checkout: Checkout) -> list[guards.Problem]:
    tip = checkout.git("rev-parse", "HEAD").strip()
    remote = checkout.git("rev-parse", "origin/main").strip()
    return guards.check_push(
        checkout.umbrella(), checkout.backend(), "origin", push_line(tip, remote)
    )


# -- pre-commit ------------------------------------------------------------


def test_check_commit_blocks_a_revision_on_no_remote_branch(
    checkout: Checkout,
) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    _stage(checkout, "sub1", landed)

    problems = guards.check_commit(checkout.umbrella())

    assert [p.name for p in problems] == ["sub1"]
    assert str(problems[0].oid) == landed


def test_check_commit_passes_once_the_commit_is_pushed(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    _stage(checkout, "sub1", landed)

    assert guards.check_commit(checkout.umbrella()) == []


def test_check_commit_ignores_a_revision_that_did_not_change(
    checkout: Checkout,
) -> None:
    assert guards.check_commit(checkout.umbrella()) == []


def test_check_commit_says_nothing_when_the_lock_is_not_staged(
    checkout: Checkout,
) -> None:
    """An edit nobody staged is not about to be committed."""
    checkout.edit("sub1", "v2")
    checkout.relock("sub1", checkout.commit("sub1", "v2"))

    assert guards.check_commit(checkout.umbrella()) == []


def test_check_commit_names_every_offending_source(checkout: Checkout) -> None:
    for name in ("sub1", "sub2"):
        checkout.edit(name, "v2")
        checkout.relock(name, checkout.commit(name, "v2"))
    checkout.git("add", "--", lock.PATH)

    problems = guards.check_commit(checkout.umbrella())

    assert sorted(p.name for p in problems) == ["sub1", "sub2"]


def test_check_commit_passes_a_source_with_no_working_copy(
    checkout: Checkout,
) -> None:
    """The lock holds nixpkgs too, and nobody clones that to develop it.

    A revision nobody here has a clone of came from a forge, so it is public
    already. Refusing it would refuse every commit after an `umbrella update`.
    """
    import shutil

    checkout.relock("sub2", "0" * 40)
    shutil.rmtree(checkout.at("sub2"))
    checkout.git("add", "--", lock.PATH)

    assert guards.check_commit(checkout.umbrella()) == []


# -- pre-push --------------------------------------------------------------


def test_check_push_blocks_a_private_revision(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    _record(checkout, "sub1", checkout.commit("sub1", "v2"), "bump sub1")

    assert [p.name for p in _check_push(checkout)] == ["sub1"]


def test_check_push_passes_when_everything_is_public(checkout: Checkout) -> None:
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    _record(checkout, "sub1", landed, "bump sub1")

    assert _check_push(checkout) == []


def test_check_push_passes_on_a_range_that_locks_nothing_new(
    checkout: Checkout,
) -> None:
    (checkout.path / "notes.txt").write_text("nothing to do with the lock\n")
    checkout.git("add", "notes.txt")
    checkout.git("commit", "-q", "-m", "unrelated")

    assert _check_push(checkout) == []


def test_check_push_reads_every_commit_in_the_range(checkout: Checkout) -> None:
    """A clone can check out an intermediate commit, so the tip is not enough."""
    checkout.edit("sub1", "orphan")
    orphan = checkout.commit("sub1", "orphan")
    _record(checkout, "sub1", orphan, "locks an orphan")

    # A second commit that does reach the remote, locked on top.
    source = checkout.at("sub1")
    if checkout.mode == "jj":
        checkout.jj("sub1", "new", "main")
    else:
        run("git", "checkout", "-q", "-B", "main", "origin/main", cwd=source)
    checkout.edit("sub1", "public")
    public = checkout.commit("sub1", "public")
    checkout.publish("sub1")
    _record(checkout, "sub1", public, "locks a public commit")

    # The tip is fine. The commit under it is not, and that is what must be caught.
    assert [str(p.oid) for p in _check_push(checkout)] == [orphan]


def test_check_push_fetches_so_a_stale_ref_does_not_block(
    checkout: Checkout,
) -> None:
    source = checkout.at("sub1")
    base = run("git", "rev-parse", "origin/main", cwd=source).strip()

    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")
    checkout.publish("sub1")
    _record(checkout, "sub1", landed, "bump sub1")

    # Rewind what this checkout believes about the remote.
    run("git", "update-ref", "refs/remotes/origin/main", base, cwd=source)

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


def test_a_branch_deletion_carries_no_revision(checkout: Checkout) -> None:
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
