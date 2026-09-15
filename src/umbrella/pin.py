"""The umbrella revision a source records inside its own tree.

`nix/sources.lock` says which revision of each source the umbrella holds. This
file says the other direction: which umbrella a source was written against. A
checkout of the source alone reads it and fetches that umbrella, so it resolves
every other dependency without asking a branch where its head is today.

**A file, and not a git ref.** A tree that Nix fetched carries no `.git`, so an
expression inside it cannot learn its own revision or its own url. Measured:
`builtins.readDir` of a fetched tree lists the source files and nothing else.
So a pin held in a ref is unreadable from a store path, from a release tarball,
and from a pull request on a fork. A file is in the tree, so every one of those
reads it. It is also the only shape that survives `--pure-eval`, where
`builtins.getEnv` answers "".

**It names the umbrella the work was written against, not the one that locks
it.** Those cannot be the same. The umbrella lock holds the source's commit
hash, so a commit holding the lock's hash would need a hash that contains
itself. The earlier revision is the useful one anyway: a build of the checkout
overrides the source with the checkout, so the umbrella supplies every *other*
source, and the revision the work was done against is the one that supplied
them.

`land` writes this, and only into a source that already has work to publish. A
project that nobody changed gains no commit.
"""

from __future__ import annotations

import re
from pathlib import Path

#: One fixed place, relative to a source's working copy.
PATH = "nix/umbrella.rev"

# The same rule `nix/sources.nix` applies on the reading side, where anything
# else falls through to the branch head. So a file holding anything else names
# no revision, and `land` replaces it with one.
_REVISION = re.compile(r"[ \n\t]*([0-9a-f]{40})[ \n\t]*\Z")


def file(workdir: Path) -> Path:
    return workdir / PATH


def carried_by(workdir: Path) -> bool:
    """Does this source record a pin at all?

    `land` never creates the file. Adopting the pin is a deliberate act in the
    project, so a source without one keeps working exactly as it did.
    """
    return file(workdir).is_file()


def read(workdir: Path) -> str | None:
    """The revision the file names, or None when it names none."""
    try:
        text = file(workdir).read_text()
    except OSError:
        return None
    found = _REVISION.fullmatch(text)
    return found.group(1) if found else None


def write(workdir: Path, revision: str) -> Path:
    target = file(workdir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{revision}\n")
    return target
