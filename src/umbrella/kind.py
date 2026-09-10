"""Whether this repo is an umbrella or a single project.

Most repos are not umbrellas, and umbrella works on those too: a plain repo
just locks nothing. The difference decides what a worktree means. For a single
project it is one more working copy. For an umbrella it is the whole
constellation.

Detection is automatic. A repo with a Nix lock coordinates the sources that
lock names, and a repo without one coordinates nobody. That is the whole test,
and it is deliberate that a project which grows a lock becomes an umbrella:
locking a sibling is what an umbrella is for.

The marker only overrides that, for a repo whose lock is a build artefact
rather than a set of projects worked on together. It lives in .git, so the
override is per checkout and never committed.
"""

from __future__ import annotations

import enum
from pathlib import Path

import pygit2

MARKER = "umbrella-kind"


class Kind(enum.StrEnum):
    SINGLE = "single"
    UMBRELLA = "umbrella"


def _marker(repo: pygit2.Repository) -> Path:
    return Path(repo.path) / MARKER


def detect(repo: pygit2.Repository) -> Kind:
    """What this repo looks like, ignoring any override."""
    from . import lock

    workdir = repo.workdir
    if workdir is None:
        return Kind.SINGLE
    return Kind.UMBRELLA if (Path(workdir) / lock.PATH).is_file() else Kind.SINGLE


def read(repo: pygit2.Repository) -> Kind:
    path = _marker(repo)
    if path.exists():
        try:
            return Kind(path.read_text().strip())
        except ValueError:
            pass
    return detect(repo)


def write(repo: pygit2.Repository, value: Kind) -> None:
    _marker(repo).write_text(f"{value}\n")


def clear(repo: pygit2.Repository) -> None:
    _marker(repo).unlink(missing_ok=True)
