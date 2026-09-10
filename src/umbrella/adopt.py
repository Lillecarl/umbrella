"""Turn a git submodule checkout into an ordinary clone.

Every umbrella that existed before this change holds its working copies as
submodules, and a submodule keeps its repository somewhere else: `<name>/.git`
is a file reading `gitdir: ../.git/modules/<name>`, and that module's config
carries a `core.worktree` pointing back.

Leaving them that way after the gitlinks are gone would be quiet and bad. The
clones keep working, so nobody notices, and their whole history sits inside the
umbrella's `.git` where one `rm -rf` takes every one of them -- including a
colocated jj repo's operation log, which is the only copy of work that is not
committed yet.

So this moves the repository into the working copy and drops the two settings
that pointed the other way. It is safe to run again: a clone that is already
ordinary is left alone.

jj needs no part in this. A colocated `.jj/repo/store/git_target` names
`../../../.git`, which is the same location before and after -- a pointer file
first, a real directory second.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pygit2

from . import gitcli
from .errors import UmbrellaError

_POINTER = "gitdir:"


def module_gitdir(workdir: Path) -> Path | None:
    """Where this clone keeps its repository, when that is not `.git` itself.

    None for an ordinary clone, and None for a directory that is no clone at
    all.
    """
    dot = workdir / ".git"
    if not dot.is_file():
        return None
    text = dot.read_text().strip()
    if not text.startswith(_POINTER):
        return None
    target = text[len(_POINTER) :].strip()
    return (workdir / target).resolve()


def adopt(workdir: Path) -> bool:
    """Move the repository into the working copy. True when something moved."""
    gitdir = module_gitdir(workdir)
    if gitdir is None:
        return False
    if not gitdir.is_dir():
        raise UmbrellaError(
            f"{workdir.name}: .git points at {gitdir}, which is not there. "
            "Nothing was moved."
        )
    linked = gitdir / "worktrees"
    if linked.is_dir() and any(linked.iterdir()):
        raise UmbrellaError(
            f"{workdir.name}: its repository has linked worktrees, and moving it "
            "would break them. Remove them first: git -C "
            f"{workdir.name} worktree list"
        )

    # Before the move, and through pygit2 rather than the git command.
    #
    # core.worktree reads `../../../<name>`, which is right from .git/modules
    # and names nothing from anywhere else. Every `git` invocation resolves it
    # while it opens the repository, before it reads a single argument, so even
    # `git config --file <path> --unset core.worktree` fails with
    #
    #   fatal: cannot chdir to '../../../sub1': No such file or directory
    #
    # pygit2.Config opens one file and no repository, so it never resolves it.
    _drop_worktree_setting(gitdir / "config")

    (workdir / ".git").unlink()
    shutil.move(str(gitdir), str(workdir / ".git"))
    return True


def _drop_worktree_setting(config: Path) -> None:
    opened = pygit2.Config(str(config))
    try:
        del opened["core.worktree"]
    except KeyError:
        pass


def forget_submodule(umbrella: Path, name: str) -> None:
    """Drop what the umbrella's config still says about a submodule.

    Not `git submodule deinit`, which removes the working copy. This only
    removes the section, and it does not care whether there was one.
    """
    try:
        gitcli.capture(umbrella, "config", "--remove-section", f"submodule.{name}")
    except gitcli.GitError:
        pass
