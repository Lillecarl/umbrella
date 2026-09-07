"""The checks that keep a private submodule commit out of public history."""

from __future__ import annotations

from dataclasses import dataclass

from pygit2 import Oid

from .backend import Backend
from .gitcli import GitError
from .jj import JjError
from .model import SHORT_ID, Sub, Umbrella


@dataclass(frozen=True)
class Problem:
    path: str
    oid: Oid
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: commit {str(self.oid)[:SHORT_ID]} {self.reason}"


def _is_zero(sha: str) -> bool:
    return set(sha) == {"0"}


def check_commit(umbrella: Umbrella) -> list[Problem]:
    """Refuse a staged pointer that no remote branch contains.

    This runs on every commit, so it does not fetch. It is fast feedback on
    local history, not the guarantee. A teammate may have pushed the commit
    already, in which case this reports a stale failure and check_push clears it.
    """
    problems = []
    staged = umbrella.staged_gitlinks()
    for sub in umbrella.subs():
        oid = staged.get(sub.path)
        if oid is None or oid == sub.recorded:
            continue
        # `present`, not `exists`. A clone made without --recurse-submodules
        # leaves the directory there and empty, and libgit2 refuses to open
        # one of those. So does a checkout that skips this submodule.
        if not sub.present:
            continue
        if not sub.on_remote(oid):
            problems.append(Problem(sub.path, oid, "is on no remote branch"))
    return problems


def check_push(
    umbrella: Umbrella, backend: Backend, remote: str, stdin: str
) -> list[Problem]:
    """Refuse to push the umbrella while a pointer it carries is private.

    Every commit in the push range becomes public, not only the tip, because a
    clone can check out an intermediate commit. Each submodule is fetched first,
    because a stale remote-tracking ref would let a private commit through.
    """
    paths = [s.path for s in umbrella.subs()]
    if not paths:
        return []

    commits = []
    for line in stdin.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = fields
        if _is_zero(local_sha):
            continue  # a branch deletion carries no new pointer
        tip = Oid(hex=local_sha)
        if tip not in umbrella.repo:
            continue
        hide = (
            umbrella.remote_branch_tips(remote)
            if _is_zero(remote_sha)
            else [Oid(hex=remote_sha)]
        )
        commits += umbrella.commits_in_range(tip, hide)

    if not commits:
        return []

    pointers = umbrella.gitlinks_introduced(commits, paths)
    if not pointers:
        return []

    subs = {s.path: s for s in umbrella.subs()}
    for path in sorted({p for p, _ in pointers}):
        _refresh(backend, subs[path])

    problems = []
    for path, oid in sorted(pointers, key=lambda item: (item[0], str(item[1]))):
        sub = subs[path]
        if not sub.present:
            problems.append(Problem(path, oid, "has no checkout here, so it cannot be verified"))
        elif not sub.contains(oid):
            problems.append(Problem(path, oid, "is not even in this checkout"))
        elif not sub.on_remote(oid):
            problems.append(Problem(path, oid, "is on no remote branch"))
    return problems


def _refresh(backend: Backend, sub: Sub) -> None:
    """Make the submodule's remote-tracking refs current."""
    if not sub.present:
        return
    try:
        backend.fetch(sub)
    except (JjError, GitError):
        pass  # offline. The check then runs on what is already known.
