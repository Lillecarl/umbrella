"""Choosing the branch that carries a source commit to its remote."""

from __future__ import annotations

import pytest

from conftest import Checkout, needs_jj, run

from umbrella import refs
from umbrella.model import Umbrella


def _choose(checkout: Checkout, name: str, declared: str | None = None) -> refs.Choice:
    umbrella = checkout.umbrella()
    source = umbrella.source(name)
    assert source is not None
    return refs.choose(
        source,
        source.head(),
        checkout.backend().default_branch(source),
        declared,
    )


def test_a_detached_checkout_reuses_its_local_branch(git_checkout: Checkout) -> None:
    """A working copy on the locked revision is detached, and main is still there."""
    sub = git_checkout.at("sub1")
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
    sub = git_checkout.at("sub1")
    run("git", "branch", "-D", "main", cwd=sub)
    assert list(git_checkout.umbrella().source("sub1").repo().branches.local) == []

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
    """The `branch` in nix/sources.nix settles it with no inference."""
    jj_checkout.jj("sub1", "bookmark", "create", "release", "-r", "main")
    jj_checkout.jj("sub1", "git", "push", "--bookmark", "release")

    jj_checkout.edit("sub1", "v2")
    jj_checkout.commit_jj("sub1", "v2")

    # main would otherwise be ambiguous with release. The declaration removes it.
    assert _choose(jj_checkout, "sub1", "release").name == "release"


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

    assert _choose(jj_checkout, "sub1", "main").name == "main"


def test_no_declaration_is_read_when_nix_cannot_answer(
    git_checkout: Checkout, monkeypatch
) -> None:
    """A checkout with no nix still lands. The declaration is a shortcut."""
    from umbrella import nixcli
    from umbrella.cli import _declared_branches

    def missing(_workdir):
        raise nixcli.NixError("nix is not here")

    monkeypatch.setattr(nixcli, "spec", missing)
    assert _declared_branches(git_checkout.umbrella()) == {}
