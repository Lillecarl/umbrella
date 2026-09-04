"""Whether this repo is an umbrella or a single project.

Most repos are not umbrellas, and umbrella works on those too: a plain repo
just has no submodules to coordinate. The difference decides what a worktree
means. For a single project it is one more working copy. For an umbrella it is
the whole constellation.

Detection is automatic. A repo with submodules is an umbrella, and one without
is not. The marker only overrides that, for a repo whose submodules are vendored
dependencies rather than projects worked on together. It lives in .git, so the
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
    workdir = repo.workdir
    if workdir is None or not (Path(workdir) / ".gitmodules").exists():
        return Kind.SINGLE
    return Kind.UMBRELLA if any(repo.submodules) else Kind.SINGLE


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
