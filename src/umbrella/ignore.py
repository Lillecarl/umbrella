"""Keep the working copies out of the umbrella's own history.

A source's working copy is an ordinary clone in a directory beside the
umbrella. Nothing records it, which is the point, so nothing must add it
either: one `git add .` in the umbrella would commit a whole other project's
tree.

.gitignore and not .git/info/exclude, for two reasons. It is committed, so
everybody who clones the umbrella gets the same protection without running
anything. And jj reads .gitignore, which is the whole shape this change was
made for.

The block is rewritten in place and everything outside it is left alone, so a
person can keep their own rules in the same file.
"""

from __future__ import annotations

from pathlib import Path

FILE = ".gitignore"

BEGIN = "# umbrella: working copies of locked sources. Fetched, never committed."
END = "# umbrella: end"


def block(names: list[str]) -> list[str]:
    return [BEGIN, *[f"/{name}/" for name in sorted(names)], END]


def write(workdir: Path, names: list[str]) -> bool:
    """Put the block in .gitignore. True when the file changed."""
    file = workdir / FILE
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
