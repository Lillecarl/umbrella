"""The Nix lock: which revision each source is at.

This is the only record of what an umbrella coordinates. `nix/sources.nix`
says where a source comes from and a human writes it. This file says which
revision, and this tool writes it.

There used to be a second record. The umbrella was a git repo with a submodule
per project, and the gitlink in its tree named a revision too. Two records for
one fact drift, and the drift was quiet: `land` moved the pointer, the lock
stayed where it was, and a build here and a build from a fresh clone then
disagreed.

The gitlink is gone. A working copy is now an ordinary clone beside the
umbrella, ignored by it, and nothing about it is recorded anywhere. The lock
is the whole of what the umbrella publishes.

Two things follow, and both are why the change was made. The umbrella can be a
jj repo, because there is no gitlink left for jj to ignore. And a checkout
needs no working copies at all: `nix/resolve.nix` reads the lock for every
source it does not find on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from pygit2 import Oid

from .errors import UmbrellaError

# One fixed place, relative to the umbrella working copy.
PATH = "nix/sources.lock"

_HEX = 40

_GITHUB = "https://github.com"


def parse(text: str, where: str = PATH) -> dict[str, dict]:
    """The sources a lock document names, node by node.

    `where` names the thing being read, so a message about a blob from an old
    commit does not claim the working copy is broken.
    """
    try:
        data = json.loads(text)
    except ValueError as error:
        raise UmbrellaError(f"{where} cannot be read: {error}") from error
    version = data.get("version") if isinstance(data, dict) else None
    if version != 1:
        raise UmbrellaError(f"{where} is version {version!r}, and this tool reads 1")
    sources = data.get("sources") or {}
    return {name: node for name, node in sources.items() if isinstance(node, dict)}


def entries(workdir: Path) -> dict[str, dict]:
    """The lock as it is written now.

    Empty when there is no lock. This reads the working copy and not the
    committed file: an edit that fixes the lock counts as fixed before it is
    committed, the same way every other row of `status` mixes the two.
    """
    file = workdir / PATH
    if not file.is_file():
        return {}
    try:
        text = file.read_text()
    except OSError as error:
        raise UmbrellaError(f"{PATH} cannot be read: {error}") from error
    return parse(text)


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


def revision(entry: dict) -> Oid | None:
    """The revision a node names, when it names one.

    A node locked by a path or by a hash alone names none, and that is not a
    fault: it is a source nothing here can move.
    """
    rev = entry.get("rev")
    if not isinstance(rev, str) or len(rev) != _HEX:
        return None
    try:
        return Oid(hex=rev)
    except ValueError:
        return None


def revisions(nodes: dict[str, dict]) -> dict[str, Oid]:
    """The revision each source is locked at, by source name."""
    found = {}
    for name, entry in nodes.items():
        rev = revision(entry)
        if rev is not None:
            found[name] = rev
    return found


def read(workdir: Path) -> dict[str, Oid]:
    """The revision the lock names for each source, straight from disk."""
    return revisions(entries(workdir))


def url(name: str, entry: dict) -> str:
    """Where to clone this source from.

    A github node carries the owner and the repository rather than a url, so
    the url is rebuilt here. https, because that is what a clone with no key
    can reach. A user who pushes over SSH sets `url.insteadOf` once in their
    own gitconfig, and git rewrites this.
    """
    kind = entry.get("type")
    if kind == "github":
        owner, repo = entry.get("owner"), entry.get("repo")
        if not owner or not repo:
            raise UmbrellaError(f"{PATH}: {name} names no owner and repository")
        return f"{_GITHUB}/{owner}/{repo}.git"
    if kind == "git":
        found = entry.get("url")
        if not found:
            raise UmbrellaError(f"{PATH}: {name} names no url")
        return str(found)
    raise UmbrellaError(f"{PATH}: {name} is of unknown type {kind!r}")
