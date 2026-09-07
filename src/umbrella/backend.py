"""One interface over the two ways to drive a submodule.

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
from .model import SHORT_ID, Sub


class Backend(Protocol):
    """What the umbrella needs from whatever drives a submodule."""

    mode: Mode

    def finalize(self, sub: Sub) -> bool:
        """Close a working commit that is already finished.

        True when something moved.
        """

    def dirty(self, sub: Sub) -> bool:
        """Does the checkout hold work that is not committed yet?"""

    def conflicted(self, sub: Sub) -> bool:
        """Is there an unresolved conflict?"""

    def default_branch(self, sub: Sub) -> str | None:
        """The branch to fall back on when nothing follows the pointer yet."""

    def advance(self, sub: Sub, name: str, exists: bool, oid: Oid) -> None:
        """Fast forward that branch onto the commit being landed."""

    def push(self, sub: Sub, branch: str) -> None: ...

    def fetch(self, sub: Sub) -> None: ...

    def move_to(self, sub: Sub, oid: Oid) -> None:
        """Put the checkout on a commit the umbrella records."""

    def hint(self, sub: Sub) -> str:
        """How to finish the work by hand."""

    def elsewhere(self, sub: Sub, head: Oid) -> list[str]:
        """Other working copies of this submodule that hold unseen work."""

    def add_working_copy(
        self, repo: Path, dest: Path, name: str, revision: str | None,
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
    """Plain git submodules. The branch already follows every commit."""

    mode = Mode.GIT

    def finalize(self, sub: Sub) -> bool:
        # Git has no working commit. Uncommitted changes are never landable.
        return False

    def dirty(self, sub: Sub) -> bool:
        # Untracked files never reach the recorded pointer, so they do not count.
        return bool(sub.repo().status(untracked_files="no", ignored=False))

    def conflicted(self, sub: Sub) -> bool:
        return sub.repo().index.conflicts is not None

    def default_branch(self, sub: Sub) -> str | None:
        repo = sub.repo()
        if repo.head_is_unborn or repo.head_is_detached:
            return None
        return repo.head.shorthand

    def advance(self, sub: Sub, name: str, exists: bool, oid: Oid) -> None:
        # Only reachable on a detached HEAD. When the branch is checked out it
        # already points at the commit, so nothing needs moving.
        if exists:
            gitcli.run(sub.workdir, "branch", "--force", name, str(oid))
        else:
            gitcli.run(sub.workdir, "branch", name, str(oid))

    def push(self, sub: Sub, branch: str) -> None:
        gitcli.run(sub.workdir, "push", _remote(sub, branch), branch)

    def fetch(self, sub: Sub) -> None:
        gitcli.fetch(sub.workdir)

    def move_to(self, sub: Sub, oid: Oid) -> None:
        # A submodule checkout is detached by design. This is what
        # git submodule update does.
        gitcli.run(sub.workdir, "checkout", "--detach", str(oid))

    def hint(self, sub: Sub) -> str:
        return f"git -C {sub.path} push"

    def elsewhere(self, sub: Sub, head: Oid) -> list[str]:
        return []  # a git worktree of a submodule is its own checkout

    def add_working_copy(
        self, repo: Path, dest: Path, name: str, revision: str | None,
        branch: str | None = None,
    ) -> None:
        # A worktree of the repo, not another clone of it. git submodule update
        # in a linked worktree clones every submodule again, with no alternates.
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
    """Colocated jj submodules. Bookmarks do not follow commits on their own."""

    mode = Mode.JJ

    def finalize(self, sub: Sub) -> bool:
        """Land the working commit itself when it is already a finished commit.

        jj commit is describe plus new, and plenty of people only do the first
        half: edit, then jj describe. That leaves @ holding finished work while
        git HEAD still points at its parent, so land used to walk straight past
        it and call it uncommitted.

        Closing it here rather than pushing @ in place matters. If @ were
        published, every later keystroke would rewrite a commit the remote
        already has.
        """
        if self.conflicted(sub):
            return False  # publishing a conflict is the one thing never to do
        if jj.working_copy_is_empty(sub.workdir):
            return False
        if not jj.working_copy_is_described(sub.workdir):
            return False  # jj itself refuses to push an undescribed commit
        jj.close_working_copy(sub.workdir)
        return True

    def dirty(self, sub: Sub) -> bool:
        return not jj.working_copy_is_empty(sub.workdir)

    def conflicted(self, sub: Sub) -> bool:
        return jj.has_conflict(sub.workdir)

    def default_branch(self, sub: Sub) -> str | None:
        return jj.trunk_bookmark(sub.workdir)

    def advance(self, sub: Sub, name: str, exists: bool, oid: Oid) -> None:
        # This is the step people forget. Without it the commit sits on no
        # bookmark, and jj git push then pushes nothing at all.
        if exists:
            jj.move_bookmark(sub.workdir, name, str(oid))
        else:
            jj.create_bookmark(sub.workdir, name, str(oid))

    def push(self, sub: Sub, branch: str) -> None:
        jj.push_bookmark(sub.workdir, branch)

    def fetch(self, sub: Sub) -> None:
        jj.fetch(sub.workdir)

    def move_to(self, sub: Sub, oid: Oid) -> None:
        # git checkout would fight the jj working copy and skip the operation log.
        jj.new(sub.workdir, str(oid))

    def hint(self, sub: Sub) -> str:
        return (
            f"jj -R {sub.path} bookmark move <name> --to @- "
            f"&& jj -R {sub.path} git push"
        )

    def elsewhere(self, sub: Sub, head: Oid) -> list[str]:
        """Workspaces holding work the umbrella cannot see.

        A jj workspace is a second working copy of the same repo, and it does
        not move git HEAD. The umbrella follows the default workspace, so work
        done in another one is invisible until a bookmark reaches it.
        """
        found = []
        for name in jj.workspaces(sub.workdir):
            if name == "default":
                continue
            if jj.work_not_reachable_from(sub.workdir, name, str(head)):
                found.append(name)
        return found

    def add_working_copy(
        self, repo: Path, dest: Path, name: str, revision: str | None,
        branch: str | None = None,
    ) -> None:
        jj.workspace_add(repo, name, dest, revision)

    def drop_working_copy(self, repo: Path, dest: Path, name: str) -> None:
        jj.workspace_forget(repo, name)

    def base_revision(self, repo: Path, requested: str | None) -> str | None:
        return jj.base_revision(repo, requested)


def for_mode(value: Mode) -> Backend:
    return JjBackend() if value is Mode.JJ else GitBackend()


def _remote(sub: Sub, branch: str) -> str:
    repo = sub.repo()
    try:
        local = repo.branches.local[branch]
        upstream = local.upstream
        if upstream is not None:
            return upstream.remote_name
    except (KeyError, pygit2.GitError):
        pass
    return "origin"
