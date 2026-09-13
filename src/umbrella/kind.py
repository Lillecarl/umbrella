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
rather than a set of projects worked on together. It lives beside the working
copy's own repository state -- see `mode.py` -- so the override is per checkout
and never committed.
"""

from __future__ import annotations

import enum
from pathlib import Path

MARKER = "umbrella-kind"


class Kind(enum.StrEnum):
    SINGLE = "single"
    UMBRELLA = "umbrella"


def detect(workdir: Path | None) -> Kind:
    """What this working copy looks like, ignoring any override."""
    from . import lock

    if workdir is None:
        return Kind.SINGLE
    return Kind.UMBRELLA if (Path(workdir) / lock.PATH).is_file() else Kind.SINGLE


def read(markers: Path, workdir: Path | None) -> Kind:
    path = markers / MARKER
    if path.exists():
        try:
            return Kind(path.read_text().strip())
        except ValueError:
            pass
    return detect(workdir)


def write(markers: Path, value: Kind) -> None:
    (markers / MARKER).write_text(f"{value}\n")


def clear(markers: Path) -> None:
    (markers / MARKER).unlink(missing_ok=True)
