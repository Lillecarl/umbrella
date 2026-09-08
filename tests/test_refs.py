"""Choosing the branch that carries a submodule commit to its remote."""

from __future__ import annotations

import pytest

from conftest import Checkout, needs_jj, run

from umbrella import refs
from umbrella.model import Umbrella


def _choose(checkout: Checkout, name: str) -> refs.Choice:
    umbrella = checkout.umbrella()
    sub = umbrella.sub(name)
    assert sub is not None
    return refs.choose(sub, sub.head(), checkout.backend().default_branch(sub))


def test_a_detached_checkout_reuses_its_local_branch(git_checkout: Checkout) -> None:
    """A submodule checkout is detached, but the clone still left main behind."""
    sub = git_checkout.sub("sub1")
    before = run("git", "rev-parse", "main", cwd=sub).strip()

    git_checkout.edit("sub1", "v2")
    run("git", "commit", "-qam", "v2", cwd=sub)

    choice = _choose(git_checkout, "sub1")
    assert choice.name == "main"
    assert str(choice.target) == before
    assert choice.needs_move


def test_a_remote_only_branch_is_used_when_no_local_branch_exists(
    git_checkout: Checkout,
) -> None:
    """Some clones leave no local branch at all. Only origin/main says the name."""
    sub = git_checkout.sub("sub1")
    run("git", "branch", "-D", "main", cwd=sub)
    assert list(git_checkout.umbrella().sub("sub1").repo().branches.local) == []

    git_checkout.edit("sub1", "v2")
    run("git", "commit", "-qam", "v2", cwd=sub)

    choice = _choose(git_checkout, "sub1")
    assert choice.name == "main"
    assert choice.target is None
    assert choice.needs_move


@needs_jj
def test_a_local_bookmark_that_follows_the_pointer_is_chosen(
    jj_checkout: Checkout,
) -> None:
    before = jj_checkout.head_of("sub1")
    jj_checkout.edit("sub1", "v2")
    landed = jj_checkout.commit_jj("sub1", "v2")

    choice = _choose(jj_checkout, "sub1")
    assert choice.name == "main"
    assert str(choice.target) == before  # the bookmark has not moved yet
    assert choice.needs_move


@needs_jj
def test_two_candidate_bookmarks_are_a_refusal(jj_checkout: Checkout) -> None:
    jj_checkout.jj("sub1", "bookmark", "create", "feature", "-r", "main")
    jj_checkout.jj("sub1", "git", "push", "--bookmark", "feature")

    jj_checkout.edit("sub1", "v2")
    jj_checkout.commit_jj("sub1", "v2")

    with pytest.raises(refs.NoBranch, match="feature, main"):
        _choose(jj_checkout, "sub1")


@needs_jj
def test_a_branch_the_commit_does_not_build_on_is_not_a_candidate(
    jj_checkout: Checkout,
) -> None:
    """Only a fast forward is ever offered, so nothing can be dropped."""
    jj_checkout.edit("sub1", "v2")
    jj_checkout.commit_jj("sub1", "v2")
    jj_checkout.jj("sub1", "bookmark", "move", "main", "--to", "@-")
    jj_checkout.jj("sub1", "git", "push", "--bookmark", "main")

    # Start a second line from the original base. main is not an ancestor of it.
    jj_checkout.jj("sub1", "new", "root()+")
    jj_checkout.edit("sub1", "sideways")
    jj_checkout.commit_jj("sub1", "sideways")

    with pytest.raises(refs.NoBranch):
        _choose(jj_checkout, "sub1")


@needs_jj
def test_a_declared_branch_wins_over_everything(jj_checkout: Checkout) -> None:
    """submodule.<name>.branch in .gitmodules settles it with no inference."""
    jj_checkout.jj("sub1", "bookmark", "create", "release", "-r", "main")
    jj_checkout.jj("sub1", "git", "push", "--bookmark", "release")
    run(
        "git",
        "config",
        "-f",
        ".gitmodules",
        "submodule.sub1.branch",
        "release",
        cwd=jj_checkout.path,
    )
    run("git", "commit", "-qam", "declare a branch", cwd=jj_checkout.path)

    jj_checkout.edit("sub1", "v2")
    jj_checkout.commit_jj("sub1", "v2")

    umbrella = jj_checkout.umbrella()
    sub = umbrella.sub("sub1")
    assert sub.declared == "release"
    # main would otherwise be ambiguous with release. The declaration removes it.
    assert refs.choose(sub, sub.head(), None).name == "release"


def test_a_dot_declaration_means_the_umbrella_own_branch(
    git_checkout: Checkout,
) -> None:
    run(
        "git",
        "config",
        "-f",
        ".gitmodules",
        "submodule.sub1.branch",
        ".",
        cwd=git_checkout.path,
    )
    run("git", "commit", "-qam", "declare dot", cwd=git_checkout.path)

    sub = git_checkout.umbrella().sub("sub1")
    assert sub.declared == "main"  # the umbrella is on main


@needs_jj
def test_a_declaration_resolves_an_ambiguity_that_would_otherwise_refuse(
    jj_checkout: Checkout,
) -> None:
    """The same repo, refused without a declaration and settled with one."""
    jj_checkout.jj("sub1", "bookmark", "create", "feature", "-r", "main")
    jj_checkout.jj("sub1", "git", "push", "--bookmark", "feature")
    jj_checkout.edit("sub1", "v2")
    jj_checkout.commit_jj("sub1", "v2")

    with pytest.raises(refs.NoBranch, match="feature, main"):
        _choose(jj_checkout, "sub1")

    run(
        "git",
        "config",
        "-f",
        ".gitmodules",
        "submodule.sub1.branch",
        "main",
        cwd=jj_checkout.path,
    )
    run("git", "commit", "-qam", "declare the branch", cwd=jj_checkout.path)

    assert _choose(jj_checkout, "sub1").name == "main"
