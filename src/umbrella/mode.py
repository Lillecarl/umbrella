"""Which VCS drives the sources in this checkout.

The choice is per checkout, not per project. One person can use jj while another
uses plain git on the same umbrella. So the marker lives beside the working
copy's own repository state -- `.git`, or `.jj` in a jj workspace, which has no
`.git` of its own. `Umbrella.markers` picks. Either way it is never committed
and never shared.
"""

from __future__ import annotations

import enum
from pathlib import Path

MARKER = "umbrella-mode"


class Mode(enum.StrEnum):
    GIT = "git"
    JJ = "jj"


def read(markers: Path) -> Mode:
    path = markers / MARKER
    if not path.exists():
        return Mode.GIT
    try:
        return Mode(path.read_text().strip())
    except ValueError:
        return Mode.GIT


def write(markers: Path, value: Mode) -> None:
    (markers / MARKER).write_text(f"{value}\n")


def clear(markers: Path) -> None:
    (markers / MARKER).unlink(missing_ok=True)
