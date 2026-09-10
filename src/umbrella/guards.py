"""The checks that keep a private commit out of a public lock.

A lock that names a revision no remote has breaks every clone but the one that
wrote it. Nix fetches the source from the forge, the forge does not have that
revision, and the evaluation fails for everybody. The person who wrote it sees
nothing, because their own working copy has the commit.

So these are the same two checks the submodule pointers used to get, asking the
same question of the same place: is this revision on a remote branch of the
source's own checkout?

Only a source with a working copy here is checked. The old check refused a
submodule with no checkout, because every submodule was meant to be checked out
and a missing one was a pointer nobody could verify. The lock is not that set:
it holds nixpkgs and every other third party, and none of those is ever checked
out. Refusing them would refuse every push that follows an `umbrella update`. A
revision nobody has a clone of came from a forge, so it is public already.

One limit, and it is the reason the umbrella can now be a jj repo. jj runs
neither hook. `jj commit` and `jj git push` go straight past them, so a jj
umbrella has fast local feedback and nothing more. The lock is still checked by
anybody on plain git, and a revision that is on no remote is visible in
`umbrella status` in either.
"""

from __future__ import annotations

from dataclasses import dataclass

from pygit2 import Oid

from . import lock
from .backend import Backend
from .gitcli import GitError
from .jj import JjError
from .model import SHORT_ID, Source, Umbrella


@dataclass(frozen=True)
class Problem:
    name: str
    oid: Oid
    reason: str

    def __str__(self) -> str:
        return f"{self.name}: commit {str(self.oid)[:SHORT_ID]} {self.reason}"


def _is_zero(sha: str) -> bool:
    return set(sha) == {"0"}


def check_commit(umbrella: Umbrella) -> list[Problem]:
    """Refuse a staged lock revision that no remote branch contains.

    This runs on every commit, so it does not fetch. It is fast feedback on
    local history, not the guarantee. A teammate may have pushed the commit
    already, in which case this reports a stale failure and check_push clears
    it.

    Only a revision the staged lock moves is checked. One that was already
    committed went through this once, and re-reporting it would refuse every
    later commit over something nobody is changing.
    """
    staged = umbrella.staged_lock()
    if staged is None:
        return []
    wanted = lock.revisions(staged)
    committed = lock.revisions(umbrella.committed_lock())

    problems = []
    for source in umbrella.sources():
        oid = wanted.get(source.name)
        if oid is None or oid == committed.get(source.name):
            continue
        if not source.present:
            continue  # no clone here to ask; see the module docstring
        if not source.on_remote(oid):
            problems.append(Problem(source.name, oid, "is on no remote branch"))
    return problems


def check_push(
    umbrella: Umbrella, backend: Backend, remote: str, stdin: str
) -> list[Problem]:
    """Refuse to push the umbrella while a revision its lock names is private.

    Every commit in the push range becomes public, not only the tip, because a
    clone can check out an intermediate commit. Each source is fetched first,
    because a stale remote-tracking ref would let a private commit through.
    """
    sources = {s.name: s for s in umbrella.sources()}
    if not sources:
        return []

    commits = []
    for line in stdin.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = fields
        if _is_zero(local_sha):
            continue  # a branch deletion carries no new revision
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

    locked = umbrella.locks_introduced(commits)
    if not locked:
        return []

    for name in sorted({name for name, _ in locked}):
        found = sources.get(name)
        if found is not None:
            _refresh(backend, found)

    problems = []
    for name, oid in sorted(locked, key=lambda item: (item[0], str(item[1]))):
        source = sources.get(name)
        if source is None or not source.present:
            continue  # no clone here to ask; see the module docstring
        if not source.contains(oid):
            problems.append(Problem(name, oid, "is not even in this checkout"))
        elif not source.on_remote(oid):
            problems.append(Problem(name, oid, "is on no remote branch"))
    return problems


def _refresh(backend: Backend, source: Source) -> None:
    """Make the source's remote-tracking refs current."""
    if not source.present:
        return
    try:
        backend.fetch(source)
    except (JjError, GitError):
        pass  # offline. The check then runs on what is already known.
