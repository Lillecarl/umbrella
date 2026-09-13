"""Thin wrapper around the jj CLI.

Every write inside a submodule goes through jj, so jj's operation log stays the
whole truth. That includes fetch and push, which keeps credential handling in jj
rather than in this program.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class JjError(RuntimeError):
    """A jj command failed."""


def _argv(repo: Path | None, args: tuple[str, ...]) -> list[str]:
    argv = ["jj", "--no-pager"]
    if repo is not None:
        argv += ["-R", str(repo)]
    return argv + list(args)


def capture(repo: Path | None, *args: str) -> str:
    """Run jj and return stdout. Raise on failure."""
    argv = _argv(repo, args)
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise JjError(f"{' '.join(argv)}\n{proc.stderr.strip()}")
    return proc.stdout


def run(repo: Path | None, *args: str) -> None:
    """Run jj, keeping its output off our stdout. Raise on failure.

    The hooks print one path on stdout for Claude to read, so nothing else may
    go there. See gitcli.run.
    """
    argv = _argv(repo, args)
    proc = subprocess.run(argv, stdout=subprocess.PIPE, text=True)
    if proc.stdout:
        print(proc.stdout, end="", file=sys.stderr)
    if proc.returncode != 0:
        raise JjError(" ".join(argv))


def is_repo(path: Path) -> bool:
    return (path / ".jj").is_dir()


def workspace_root(start: Path) -> Path | None:
    """The nearest working copy up from `start` that is jj and not git.

    A colocated repo has both, and everything that reads it wants the git one,
    so this passes it by. A jj workspace has only `.jj`, and git discovery
    walks straight through it -- to nothing when the workspace sits outside the
    repo it came from, and to the wrong tree when it sits inside. Stopping here
    is what makes `umbrella` answer for the workspace it is standing in.

    Read without running jj. This is on the path of every command.
    """
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return None
        if (directory / ".jj").is_dir():
            return directory
    return None


def git_dir_of(workspace: Path) -> Path | None:
    """The git directory behind a jj workspace, or None if it has no git store.

    `.jj/repo` is the repository. In a workspace it is a file naming the one
    the workspace was added to; in the original it is the directory itself.
    `<repo>/store/git_target` then names the git directory, relative to itself.
    """
    repo = workspace / ".jj" / "repo"
    if repo.is_file():
        repo = (repo.parent / repo.read_text().strip()).resolve()
    target = repo / "store" / "git_target"
    if not target.is_file():
        return None
    return (target.parent / target.read_text().strip()).resolve()


def init_colocate(path: Path) -> None:
    """Colocate a jj repo onto an existing git checkout.

    jj git init takes the path as an argument. -R only opens a repo that already
    exists, so it cannot be used here.
    """
    run(None, "git", "init", "--colocate", str(path))


def working_copy_is_empty(repo: Path) -> bool:
    return (
        capture(repo, "log", "--no-graph", "-r", "@", "-T", "empty").strip() == "true"
    )


def has_conflict(repo: Path) -> bool:
    """Is anything the working copy descends from conflicted?

    jj materializes conflict markers into the file and commits them. git reports
    plain modified content, so the umbrella cannot see a conflict on its own.
    """
    revset = "conflicts() & ::@"
    out = capture(repo, "log", "--no-graph", "-r", revset, "-T", 'commit_id ++ "\n"')
    return bool(out.strip())


def working_copy_is_described(repo: Path) -> bool:
    template = 'if(description, "yes", "no")'
    return (
        capture(repo, "log", "--no-graph", "-r", "@", "-T", template).strip() == "yes"
    )


def close_working_copy(repo: Path) -> None:
    """Start a fresh working commit on top of the current one.

    This is the second half of jj commit. It leaves the finished work as @-,
    which is what git HEAD points at, so everything downstream sees a normal
    committed state.
    """
    run(repo, "new")


def workspaces(repo: Path) -> list[str]:
    """Every workspace name, including default."""
    out = capture(repo, "workspace", "list", "-T", 'name ++ "\n"')
    return [line.strip() for line in out.splitlines() if line.strip()]


def work_not_reachable_from(repo: Path, workspace: str, base: str) -> list[str]:
    """Commits a workspace holds that base cannot reach.

    Empty commits are excluded: a workspace always has one at the tip, and it
    carries nothing to publish.
    """
    revset = f"::{workspace}@ ~ ::{base} ~ empty()"
    out = capture(repo, "log", "--no-graph", "-r", revset, "-T", 'commit_id ++ "\n"')
    return [line.strip() for line in out.splitlines() if line.strip()]


def workspace_add(repo: Path, name: str, dest: Path, revision: str | None) -> None:
    """A second working copy of this repo. It shares the repo, so it is cheap.

    A revision of None lets jj pick, which puts the workspace beside the
    current one.
    """
    args = ["workspace", "add", "--name", name]
    if revision is not None:
        args += ["-r", revision]
    run(repo, *args, str(dest))


def base_revision(repo: Path, requested: str | None) -> str | None:
    """Where a new workspace should start.

    trunk() silently resolves to root() in a repo with no trunk-like bookmark
    on a remote, which is most local-only repos. Forking from root() would give
    an empty workspace, so fall back to letting jj choose.
    """
    revset = requested or os.environ.get("JJ_WORKTREES_BASE_REVSET") or "trunk()"
    if revset == "trunk()":
        found = capture(
            repo, "log", "--no-graph", "-r", "trunk() ~ root()", "-T", "commit_id"
        )
        if not found.strip():
            return None
    return revset


def workspace_forget(repo: Path, name: str) -> None:
    run(repo, "workspace", "forget", name)


def trunk_bookmark(repo: Path) -> str | None:
    """Name of the local bookmark that trunk() resolves to."""
    template = 'local_bookmarks.map(|b| b.name()).join("\n")'
    out = capture(repo, "log", "--no-graph", "-r", "trunk()", "-T", template)
    names = [line for line in out.splitlines() if line.strip()]
    return names[0] if names else None


def untracked_remote_bookmarks(repo: Path) -> list[str]:
    """Remote bookmarks that no local bookmark follows, as name@remote."""
    template = (
        'if(remote && remote != "git" && !tracked, name ++ "@" ++ remote ++ "\n")'
    )
    out = capture(repo, "bookmark", "list", "--all-remotes", "-T", template)
    return [line.strip() for line in out.splitlines() if line.strip()]


def track(repo: Path, bookmark: str) -> None:
    run(repo, "bookmark", "track", bookmark)


def fetch(repo: Path) -> None:
    run(repo, "git", "fetch")


def push_bookmark(repo: Path, bookmark: str) -> None:
    run(repo, "git", "push", "--bookmark", bookmark)


def move_bookmark(repo: Path, bookmark: str, to: str) -> None:
    run(repo, "bookmark", "move", bookmark, "--to", to)


def create_bookmark(repo: Path, bookmark: str, at: str) -> None:
    run(repo, "bookmark", "create", bookmark, "-r", at)


def new(repo: Path, revision: str) -> None:
    """Move the working copy onto a commit without a git checkout.

    git checkout inside a submodule fights the jj working copy and bypasses the
    jj operation log. jj new does the same job through jj.
    """
    run(repo, "new", revision)
