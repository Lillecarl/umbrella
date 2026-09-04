"""Which VCS drives the submodules in this checkout.

The choice is per checkout, not per project. One person can use jj while another
uses plain git on the same umbrella. So the marker lives inside the .git
directory: it is never committed and never shared.
"""

from __future__ import annotations

import enum
from pathlib import Path

import pygit2

MARKER = "umbrella-mode"


class Mode(enum.StrEnum):
    GIT = "git"
    JJ = "jj"


def _marker(repo: pygit2.Repository) -> Path:
    return Path(repo.path) / MARKER


def read(repo: pygit2.Repository) -> Mode:
    path = _marker(repo)
    if not path.exists():
        return Mode.GIT
    try:
        return Mode(path.read_text().strip())
    except ValueError:
        return Mode.GIT


def write(repo: pygit2.Repository, value: Mode) -> None:
    _marker(repo).write_text(f"{value}\n")


def clear(repo: pygit2.Repository) -> None:
    _marker(repo).unlink(missing_ok=True)
