"""One interface over the two ways to drive a source.

The guards do not appear here. They ask git questions only, so they work the
same whichever backend is in use.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

import pygit2
from pygit2 import Oid

from . import gitcli, jj
from .mode import Mode
from .model import SHORT_ID, Source


class Backend(Protocol):
    """What the umbrella needs from whatever drives a source."""

    mode: Mode

    def finalize(self, source: Source) -> bool:
        """Close a working commit that is already finished.

        True when something moved.
        """

    def dirty(self, source: Source) -> bool:
        """Does the checkout hold work that is not committed yet?"""

    def conflicted(self, source: Source) -> bool:
        """Is there an unresolved conflict?"""

    def default_branch(self, source: Source) -> str | None:
        """The branch to fall back on when nothing follows the pointer yet."""

    def advance(self, source: Source, name: str, exists: bool, oid: Oid) -> None:
        """Fast forward that branch onto the commit being landed."""

    def push(self, source: Source, branch: str) -> None: ...

    def fetch(self, source: Source) -> None: ...

    def move_to(self, source: Source, oid: Oid) -> None:
        """Put the checkout on the commit the lock names."""

    def hint(self, source: Source) -> str:
        """How to finish the work by hand."""

    def elsewhere(self, source: Source, head: Oid) -> list[str]:
        """Other working copies of this source that hold unseen work."""

    def working_copies(self, source: Source) -> list[str]:
        """Other working copies of this source, whatever they hold.

        `elsewhere` asks which of them hold work nobody has seen. This asks
        which of them exist at all, because removing the checkout they share
        their storage with would strand every one of them.
        """

    def add_working_copy(
        self,
        repo: Path,
        dest: Path,
        name: str,
        revision: str | None,
        branch: str | None = None,
    ) -> None:
        """One more working copy of a repo, sharing its storage.

        branch names a branch to create in git mode. None means detached.
        """

    def drop_working_copy(self, repo: Path, dest: Path, name: str) -> None:
        """Undo add_working_copy."""

    def base_revision(self, repo: Path, requested: str | None) -> str | None:
        """Where a new working copy should start."""


class GitBackend:
    """Plain git clones. The branch already follows every commit."""

    mode = Mode.GIT

    def finalize(self, source: Source) -> bool:
        # Git has no working commit. Uncommitted changes are never landable.
        return False

    def dirty(self, source: Source) -> bool:
        # Untracked files never reach a commit, so they never reach the lock.
        return bool(source.repo().status(untracked_files="no", ignored=False))

    def conflicted(self, source: Source) -> bool:
        return source.repo().index.conflicts is not None

    def default_branch(self, source: Source) -> str | None:
        repo = source.repo()
        if repo.head_is_unborn or repo.head_is_detached:
            return None
        return repo.head.shorthand

    def advance(self, source: Source, name: str, exists: bool, oid: Oid) -> None:
        # Only reachable on a detached HEAD. When the branch is checked out it
        # already points at the commit, so nothing needs moving.
        if exists:
            gitcli.run(source.workdir, "branch", "--force", name, str(oid))
        else:
            gitcli.run(source.workdir, "branch", name, str(oid))

    def push(self, source: Source, branch: str) -> None:
        gitcli.run(source.workdir, "push", _remote(source, branch), branch)

    def fetch(self, source: Source) -> None:
        gitcli.fetch(source.workdir)

    def move_to(self, source: Source, oid: Oid) -> None:
        # Detached, so that no branch is dragged onto the locked revision. The
        # branches in this clone are the person's own work.
        gitcli.run(source.workdir, "checkout", "--detach", str(oid))

    def hint(self, source: Source) -> str:
        return f"git -C {source.name} push"

    def elsewhere(self, source: Source, head: Oid) -> list[str]:
        return []  # a git worktree of a source is its own checkout

    def working_copies(self, source: Source) -> list[str]:
        """The linked worktrees, and only those.

        libgit2 lists exactly the linked ones, so the checkout itself never
        appears in the answer.
        """
        return list(source.repo().list_worktrees())

    def add_working_copy(
        self,
        repo: Path,
        dest: Path,
        name: str,
        revision: str | None,
        branch: str | None = None,
    ) -> None:
        # A worktree of the repo, not another clone of it, so the two share
        # one object store.
        if branch is None:
            gitcli.worktree_add_detached(repo, dest, revision or "HEAD")
        else:
            gitcli.worktree_add(repo, dest, branch, revision)

    def drop_working_copy(self, repo: Path, dest: Path, name: str) -> None:
        gitcli.worktree_remove(repo, dest)
        # git keeps the branch after the worktree is gone, so the same name
        # could not be used twice. An agent reuses names constantly.
        left = gitcli.drop_branch(repo, f"worktree/{name}")
        if left is not None:
            print(
                f"worktree/{name} held {str(left)[:SHORT_ID]}, which is on no other "
                "branch. It is still in the reflog.",
                file=sys.stderr,
            )

    def base_revision(self, repo: Path, requested: str | None) -> str | None:
        return requested or "HEAD"


class JjBackend:
    """Colocated jj clones. Bookmarks do not follow commits on their own."""

    mode = Mode.JJ

    def finalize(self, source: Source) -> bool:
        """Land the working commit itself when it is already a finished commit.

        jj commit is describe plus new, and plenty of people only do the first
        half: edit, then jj describe. That leaves @ holding finished work while
        git HEAD still points at its parent, so land used to walk straight past
        it and call it uncommitted.

        Closing it here rather than pushing @ in place matters. If @ were
        published, every later keystroke would rewrite a commit the remote
        already has.
        """
        if self.conflicted(source):
            return False  # publishing a conflict is the one thing never to do
        if jj.working_copy_is_empty(source.workdir):
            return False
        if not jj.working_copy_is_described(source.workdir):
            return False  # jj itself refuses to push an undescribed commit
        jj.close_working_copy(source.workdir)
        return True

    def dirty(self, source: Source) -> bool:
        return not jj.working_copy_is_empty(source.workdir)

    def conflicted(self, source: Source) -> bool:
        return jj.has_conflict(source.workdir)

    def default_branch(self, source: Source) -> str | None:
        return jj.trunk_bookmark(source.workdir)

    def advance(self, source: Source, name: str, exists: bool, oid: Oid) -> None:
        # This is the step people forget. Without it the commit sits on no
        # bookmark, and jj git push then pushes nothing at all.
        if exists:
            jj.move_bookmark(source.workdir, name, str(oid))
        else:
            jj.create_bookmark(source.workdir, name, str(oid))

    def push(self, source: Source, branch: str) -> None:
        jj.push_bookmark(source.workdir, branch)

    def fetch(self, source: Source) -> None:
        jj.fetch(source.workdir)

    def move_to(self, source: Source, oid: Oid) -> None:
        # git checkout would fight the jj working copy and skip the operation log.
        jj.new(source.workdir, str(oid))

    def hint(self, source: Source) -> str:
        return (
            f"jj -R {source.name} bookmark move <name> --to @- "
            f"&& jj -R {source.name} git push"
        )

    def elsewhere(self, source: Source, head: Oid) -> list[str]:
        """Workspaces holding work the umbrella cannot see.

        A jj workspace is a second working copy of the same repo, and it does
        not move git HEAD. The umbrella follows the default workspace, so work
        done in another one is invisible until a bookmark reaches it.
        """
        found = []
        for name in jj.workspaces(source.workdir):
            if name == "default":
                continue
            if jj.work_not_reachable_from(source.workdir, name, str(head)):
                found.append(name)
        return found

    def working_copies(self, source: Source) -> list[str]:
        return [name for name in jj.workspaces(source.workdir) if name != "default"]

    def add_working_copy(
        self,
        repo: Path,
        dest: Path,
        name: str,
        revision: str | None,
        branch: str | None = None,
    ) -> None:
        jj.workspace_add(repo, name, dest, revision)

    def drop_working_copy(self, repo: Path, dest: Path, name: str) -> None:
        jj.workspace_forget(repo, name)

    def base_revision(self, repo: Path, requested: str | None) -> str | None:
        return jj.base_revision(repo, requested)


def for_mode(value: Mode) -> Backend:
    return JjBackend() if value is Mode.JJ else GitBackend()


def _remote(source: Source, branch: str) -> str:
    repo = source.repo()
    try:
        local = repo.branches.local[branch]
        upstream = local.upstream
        if upstream is not None:
            return upstream.remote_name
    except (KeyError, pygit2.GitError):
        pass
    return "origin"
