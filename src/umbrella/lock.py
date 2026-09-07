"""What a Nix lock file names, next to what the umbrella records.

An umbrella records a submodule pointer in its tree. A lock file beside it
records a revision for the same project. Both say "this is the commit", and
nothing keeps them together: `land` moves the pointer and the lock stays where
it was.

The drift is quiet, which is why it needs a row. A checkout with working copies
never reads the lock for a project it has on disk, so the build is right here
and wrong for everybody who takes the umbrella at a revision. Nix cannot see it
either, because Nix cannot read the git index.

The file is optional. An umbrella without one loses nothing: `status` says
nothing about it, and only `update` writes it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pygit2 import Oid

from .model import UmbrellaError

# One fixed place, relative to the umbrella working copy.
PATH = "nix/sources.lock"

_HEX = 40


def entries(workdir: Path) -> dict[str, dict]:
    """The lock as it is written, node by node.

    Empty when there is no lock. This reads the working copy and not the
    committed file: an edit that fixes the lock should count as fixed before it
    is committed, the same way every other row of `status` mixes the two.
    """
    file = workdir / PATH
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError) as error:
        raise UmbrellaError(f"{PATH} cannot be read: {error}") from error
    version = data.get("version") if isinstance(data, dict) else None
    if version != 1:
        raise UmbrellaError(f"{PATH} is version {version!r}, and this tool reads 1")
    sources = data.get("sources") or {}
    return {name: node for name, node in sources.items() if isinstance(node, dict)}


def write(workdir: Path, sources: dict[str, dict]) -> Path:
    """Write the lock, sorted throughout.

    Sorted so that a run that changes one revision has a one line diff. A tool
    writes this file and a human reads the diff, which is the whole reason the
    specification and the lock are two files.
    """
    file = workdir / PATH
    file.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "sources": sources}
    file.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return file


def read(workdir: Path) -> dict[str, Oid]:
    """The revision the lock names for each source, by source name.

    A source with no revision is skipped. Some are locked by a path or by a
    hash alone, and neither answers this question.
    """
    found: dict[str, Oid] = {}
    for name, entry in entries(workdir).items():
        rev = entry.get("rev")
        if isinstance(rev, str) and len(rev) == _HEX:
            try:
                found[name] = Oid(hex=rev)
            except ValueError:
                continue
    return found
