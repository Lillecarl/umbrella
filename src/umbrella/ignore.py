"""Keep the working copies out of the umbrella's own history.

A source's working copy is an ordinary clone in a directory beside the
umbrella. Nothing records it, which is the point, so nothing must add it
either: one `git add .` in the umbrella would commit a whole other project's
tree.

.git/info/exclude and not .gitignore. A committed .gitignore would be a
second derived record of what the lock already names, written by a tool and
read as a diff -- which is the exact shape this program was rebuilt to stop
having. Two people on two builds of umbrella would write two blocks. Worse in
a jj umbrella, where rewriting a tracked file puts it straight into the
working commit.

Measured on jj 0.44.0, colocated: a directory covered only by
.git/info/exclude is invisible to `git status` and to `jj status` alike. It is
also what `hooks.exclude` already uses for the generated .githooks directory,
so this is one mechanism rather than two.

Every name in the lock is written, not only the ones on disk. So one
`umbrella init` covers a source cloned here by hand later, and there is no
window where a directory exists and nothing excludes it.

The file is not shared, and that is the trade. A clone nobody has run
`umbrella init` in has no protection -- and no working copies either, so
nothing to protect.

The block is rewritten in place and everything outside it is left alone, so a
person keeps their own rules in the same file.
"""

from __future__ import annotations

from pathlib import Path

import pygit2

FILE = "info/exclude"

BEGIN = "# umbrella: working copies of locked sources. Fetched, never committed."
END = "# umbrella: end"


def common_dir(repo: pygit2.Repository) -> Path:
    """The git directory shared by every worktree of this repo.

    A linked worktree has its own git directory holding HEAD, the index and
    such, plus a commondir file pointing back at the shared one. git reads
    info/exclude only from the shared one, so writing it into a linked
    worktree's own git directory silently does nothing.
    """
    path = Path(repo.path)
    pointer = path / "commondir"
    if not pointer.exists():
        return path
    return (path / pointer.read_text().strip()).resolve()


def block(names: list[str]) -> list[str]:
    return [BEGIN, *[f"/{name}/" for name in sorted(names)], END]


def write(repo: pygit2.Repository, names: list[str]) -> bool:
    """Put the block in info/exclude. True when the file changed."""
    file = common_dir(repo) / FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    lines = file.read_text().splitlines() if file.is_file() else []

    wanted = block(names)
    try:
        start = lines.index(BEGIN)
    except ValueError:
        rebuilt = lines + ([""] if lines and lines[-1] != "" else []) + wanted
    else:
        try:
            stop = lines.index(END, start)
        except ValueError:
            stop = len(lines) - 1
        rebuilt = lines[:start] + wanted + lines[stop + 1 :]

    if rebuilt == lines:
        return False
    file.write_text("\n".join(rebuilt) + "\n")
    return True
