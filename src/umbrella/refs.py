"""Pick the branch that carries a source commit to its remote.

Both modes need one. git advances a branch on commit, jj does not, but the
question is the same either way: which ref should the remote end up pointing at?

There are two ways to know which. The good one is that nix/sources.nix says
so: every source declares a `branch`, and that file is committed, so everyone
sees the same answer. Declare it and there is nothing to infer.

Without it the branch has to be derived: take the one whose remote counterpart
contains the revision the lock names today. That stays right when a project's
default branch is not main, and when the work sits on a feature branch.

The declaration is optional here because reading it needs nix, and a checkout
with no nix, or a specification that does not evaluate, must still be able to
land. The caller passes what it could read.

Either way only a fast forward is offered, so no choice here can drop a commit.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygit2
from pygit2 import Oid, Repository

from .model import Source


@dataclass(frozen=True)
class Choice:
    """The branch to push, and where it sits now."""

    name: str
    target: Oid | None  # None when the branch does not exist yet
    needs_move: bool


class NoBranch(Exception):
    """No single branch can be chosen."""


def _remote_target(repo: Repository, name: str) -> Oid | None:
    """Where the remote counterpart of a local branch points."""
    try:
        upstream = repo.branches.local[name].upstream
    except (KeyError, pygit2.GitError):
        upstream = None
    if upstream is None:
        # jj does not write git upstream config, so fall back to the name.
        for remote_name in repo.branches.remote:
            if remote_name.split("/", 1)[-1] == name:
                upstream = repo.branches.remote[remote_name]
                break
    if upstream is None:
        return None
    target = upstream.target
    return target if isinstance(target, Oid) else None


def _reaches(repo: Repository, tip: Oid, other: Oid) -> bool:
    """Is other an ancestor of tip, or the same commit?

    A commit this repo has never fetched reaches nothing and is reached by
    nothing. That happens whenever the umbrella has been pulled but the
    source has not, and asking libgit2 about it raises instead of answering.
    """
    if tip == other:
        return True
    if tip not in repo or other not in repo:
        return False
    return repo.descendant_of(tip, other)


def _local_candidates(
    repo: Repository, source: Source, head: Oid
) -> list[tuple[str, Oid]]:
    found = []
    for name in repo.branches.local:
        target = repo.branches.local[name].target
        if not isinstance(target, Oid) or not _reaches(repo, head, target):
            continue  # not a fast forward, so not ours to move
        remote_target = _remote_target(repo, name)
        if remote_target is None:
            continue  # never pushed, so the lock cannot be following it
        if source.locked is not None and not _reaches(
            repo, remote_target, source.locked
        ):
            continue  # the lock does not follow this branch
        found.append((name, target))
    return found


def _remote_candidates(repo: Repository, source: Source, head: Oid) -> list[str]:
    """Branch names that exist only on the remote.

    A fresh source checkout is detached with no local branch at all, so this
    is the normal case the first time someone lands work in it.
    """
    found = set()
    for full in repo.branches.remote:
        try:
            target = repo.branches.remote[full].target
        except (KeyError, TypeError):
            continue
        if not isinstance(target, Oid):
            continue  # a symbolic ref such as origin/HEAD
        if not _reaches(repo, head, target):
            continue  # landing here would not be a fast forward
        if source.locked is not None and not _reaches(repo, target, source.locked):
            continue  # the lock does not follow this branch
        found.add(full.split("/", 1)[-1])
    return sorted(found)


def _from_declaration(repo: Repository, source: Source, head: Oid, name: str) -> Choice:
    """The specification named the branch, so there is nothing to choose."""
    try:
        target = repo.branches.local[name].target
    except KeyError:
        target = None
    if not isinstance(target, Oid):
        target = None

    for where, position in (
        ("locally", target),
        ("on the remote", _remote_target(repo, name)),
    ):
        if position is None or _reaches(repo, head, position):
            continue
        if _reaches(repo, position, head):
            raise NoBranch(
                f"{source.name}: {name} has moved ahead {where} of the commit to "
                "land. Fetch it and rebase onto it first."
            )
        raise NoBranch(
            f"{source.name}: {name} {where} has diverged from the commit to land. "
            "Landing it would not be a fast forward."
        )
    return Choice(name=name, target=target, needs_move=target != head)


def choose(
    source: Source, head: Oid, fallback: str | None, declared: str | None = None
) -> Choice:
    """Which branch should carry `head` to the remote?

    Only a fast forward is ever offered. A branch that `head` does not already
    build on is never a candidate, so this can never drop a commit.
    """
    repo = source.repo()

    if declared is not None:
        return _from_declaration(repo, source, head, declared)

    local = _local_candidates(repo, source, head)
    if len(local) > 1:
        names = ", ".join(sorted(name for name, _ in local))
        raise NoBranch(
            f"{source.name}: several branches could carry this commit ({names}). "
            "Move the one you mean yourself, then run land again."
        )
    if local:
        name, target = local[0]
        return Choice(name=name, target=target, needs_move=target != head)

    remote = _remote_candidates(repo, source, head)
    if len(remote) > 1:
        raise NoBranch(
            f"{source.name}: several remote branches could carry this commit "
            f"({', '.join(remote)}). Create the local one you mean, then land."
        )
    if remote:
        return Choice(name=remote[0], target=None, needs_move=True)

    if fallback is None:
        raise NoBranch(
            f"{source.name}: no branch follows the locked revision, and there is no "
            "default to fall back on. Create one and push it."
        )
    try:
        target = repo.branches.local[fallback].target
    except KeyError:
        target = None
    if target is not None and not _reaches(repo, head, target):
        raise NoBranch(
            f"{source.name}: {fallback} is not an ancestor of the commit to land. "
            "Moving it would not be a fast forward, so land will not guess."
        )
    return Choice(name=fallback, target=target, needs_move=target != head)


def remote_ahead(source: Source, head: Oid) -> str | None:
    """A remote branch this source follows that holds commits head lacks.

    Read from the remote-tracking refs, so it is only as fresh as the last
    fetch. It answers "did someone else move this while I was working".
    """
    repo = source.repo()
    for full in repo.branches.remote:
        try:
            target = repo.branches.remote[full].target
        except (KeyError, TypeError):
            continue
        if not isinstance(target, Oid):
            continue  # a symbolic ref such as origin/HEAD
        if source.locked is not None and not _reaches(repo, target, source.locked):
            continue  # not a branch the lock follows
        if _reaches(repo, head, target):
            continue  # already have it
        return full
    return None
