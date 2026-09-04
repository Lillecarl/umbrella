"""Git hooks that call back into this program.

A hook must work when git runs it from a bare environment, so it cannot rely on
the dev shell. It also must not carry a nix store path in a committed file,
because that would rot on the next rebuild.

So the hook prefers umbrella on PATH, and falls back to a path recorded inside
.git at init time. The .git directory is never committed, so the store path
stays out of version control and stays local to this checkout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pygit2

HOOKS_DIR = ".githooks"
EXE_MARKER = "umbrella-exe"

_PREAMBLE = """#!/bin/sh
exe=$(command -v umbrella 2>/dev/null)
if [ -z "$exe" ]; then
  recorded="$(git rev-parse --git-dir)/{marker}"
  [ -r "$recorded" ] && exe=$(cat "$recorded")
fi
if [ -z "$exe" ] || [ ! -x "$exe" ]; then
  echo "umbrella is not available. Run it once to record it, or enter the dev shell." >&2
  exit 1
fi
"""

_BODIES = {
    "pre-commit": "exec \"$exe\" check-commit\n",
    "pre-push": "exec \"$exe\" check-push \"$@\"\n",
}


def executable() -> str:
    """The command the hooks should call.

    UMBRELLA_EXE overrides it. That is what the test suite uses, and it also
    lets anyone pin a specific build.
    """
    override = os.environ.get("UMBRELLA_EXE")
    return override if override else os.path.realpath(sys.argv[0])


def common_dir(repo: pygit2.Repository) -> Path:
    """The git directory shared by every worktree of this repo.

    A linked worktree has its own git directory holding HEAD, the index and
    such, plus a commondir file pointing back at the shared one.
    """
    path = Path(repo.path)
    pointer = path / "commondir"
    if not pointer.exists():
        return path
    return (path / pointer.read_text().strip()).resolve()


def exclude(repo: pygit2.Repository, entry: str) -> None:
    """Ignore something locally.

    Generated files do not belong in the project's .gitignore, and the exclude
    file is never committed. It has to go in the common git directory: git
    reads info/exclude only from there, so writing it into a linked worktree's
    own git directory silently does nothing.
    """
    path = common_dir(repo) / "info" / "exclude"
    path.parent.mkdir(exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    if entry not in lines:
        lines.append(entry)
        path.write_text("\n".join(lines) + "\n")


def install(repo: pygit2.Repository) -> list[str]:
    workdir = Path(repo.workdir)
    directory = workdir / HOOKS_DIR
    directory.mkdir(exist_ok=True)

    (Path(repo.path) / EXE_MARKER).write_text(f"{executable()}\n")

    exclude(repo, f"/{HOOKS_DIR}/")

    written = []
    for name, body in _BODIES.items():
        path = directory / name
        path.write_text(_PREAMBLE.format(marker=EXE_MARKER) + body)
        path.chmod(0o755)
        written.append(name)
    return written
