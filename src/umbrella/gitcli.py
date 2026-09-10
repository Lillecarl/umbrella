"""The few git operations libgit2 would need credentials for."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pygit2
from pygit2 import Oid


class GitError(RuntimeError):
    """A git command failed."""


def run(cwd: Path, *args: str) -> None:
    """Run git, keeping its chatter off our stdout.

    git worktree add says "HEAD is now at ..." on stdout. The hooks print the
    directory they made on stdout and Claude reads it, so anything else there
    corrupts the answer. None of this output is a result, so it belongs on
    stderr with the rest of the diagnostics.
    """
    argv = ["git", *args]
    proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, text=True)
    if proc.stdout:
        print(proc.stdout, end="", file=sys.stderr)
    if proc.returncode != 0:
        raise GitError(" ".join(argv))


def capture(cwd: Path, *args: str) -> str:
    argv = ["git", *args]
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"{' '.join(argv)}\n{proc.stderr.strip()}")
    return proc.stdout


def clone(cwd: Path, url: str, dest: Path) -> None:
    """Clone a source beside the umbrella.

    `cwd` decides nothing about the answer; it only puts the command inside the
    checkout whose configuration holds any url rewrite the user set, so
    somebody who pushes over SSH clones over SSH.
    """
    run(cwd, "clone", "--quiet", url, str(dest))


def fetch(repo: Path) -> None:
    run(repo, "fetch", "--all", "--prune", "--quiet")


def remote_head(cwd: Path, url: str, branch: str) -> str | None:
    """Where a branch points on a remote, without cloning it.

    None when the remote has no such branch. `cwd` decides nothing about the
    answer; it only puts the command inside the checkout whose configuration
    holds any url rewrite the user set.
    """
    out = capture(cwd, "ls-remote", url, f"refs/heads/{branch}")
    first = out.split("\n", 1)[0].strip()
    return first.split("\t", 1)[0] if first else None


def push(umbrella: Path) -> None:
    run(umbrella, "push")


def worktree_add(
    repo: Path, dest: Path, branch: str, revision: str | None = None
) -> None:
    args = ["worktree", "add", "-b", branch, str(dest)]
    if revision is not None:
        args.append(revision)
    run(repo, *args)


def worktree_add_detached(repo: Path, dest: Path, revision: str) -> None:
    """A second checkout of this repo. Unlike a submodule clone it shares
    the object store, so it costs almost nothing."""
    run(repo, "worktree", "add", "--detach", str(dest), revision)


def worktree_remove(repo: Path, dest: Path) -> None:
    run(repo, "worktree", "remove", "--force", str(dest))


def drop_branch(repo: Path, name: str) -> Oid | None:
    """Remove a branch this program created.

    Returns its tip when HEAD could not reach it, so a caller can say where the
    commits went instead of letting them disappear quietly. They are still in
    the reflog either way.
    """
    try:
        opened = pygit2.Repository(str(repo))
        branch = opened.branches.local.get(name)
    except pygit2.GitError:
        return None
    if branch is None:
        return None
    tip = branch.target
    unreachable = None
    if isinstance(tip, Oid) and not opened.head_is_unborn:
        head = opened.head.target
        if head != tip and not opened.descendant_of(head, tip):
            unreachable = tip
    opened.branches.local.delete(name)
    return unreachable


def worktree_paths(repo: Path) -> list[Path]:
    out = capture(repo, "worktree", "list", "--porcelain")
    return [
        Path(line.split(" ", 1)[1])
        for line in out.splitlines()
        if line.startswith("worktree ")
    ]
