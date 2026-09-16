"""The refs that let a standalone checkout find the umbrella that locks it.

**A child cannot hold the revision of the umbrella that locks it.** The
umbrella's commit holds the child's hash, so a child commit holding the
umbrella's hash would need a hash that contains itself. Measured over six
rounds in `docs/locking.md`: twelve commits, and the umbrella's lock was
never true of the current child.

A ref escapes that, because a ref is not in the tree. The umbrella publishes

    refs/umbrella/<source>/<source revision>  ->  the umbrella commit that locks it

and no child gains a commit. A standalone checkout reads its own revision out
of `.git/HEAD` and fetches that ref, and what comes back **is** the umbrella
tree -- one fetch, nothing to parse. Measured end to end in `docs/locking.md`.

**One ref per source revision, written once.** That is what makes it better
than a branch: a stale fetcher cache of an immutable ref still gives the
right answer, where a stale cache of the branch head gives last hour's. A
consumer pinned to a fixed nixidae watched its render hash move on its own
because of exactly that.

**The first umbrella to lock a revision keeps it.** A source that nobody
touched stays locked at the same revision across many umbrella commits, so
several of them could claim the ref. Taking the newest would make the ref
mutable again and give back the property above. The earliest is also the
honest answer: it is the umbrella that first saw this revision.
"""

from __future__ import annotations

from dataclasses import dataclass

from pygit2 import Oid, Repository

PREFIX = "refs/umbrella"


def name(source: str, revision: Oid | str) -> str:
    """The ref that maps one revision of one source to its umbrella."""
    return f"{PREFIX}/{source}/{revision}"


@dataclass(frozen=True)
class Mark:
    """One ref to write."""

    source: str
    revision: Oid
    ref: str


def ours(
    spec: dict[str, dict], locked: dict[str, Oid]
) -> tuple[dict[str, Oid], bool]:
    """The locked revisions worth mapping, and whether the spec narrowed them.

    **Only the repositories worked on together here.** A ref exists so a
    standalone checkout can find its umbrella, and nobody checks out nixpkgs
    to build it against this umbrella. Mapping every source would add a ref
    for every nixpkgs bump, forever, for nothing.

    `path` in `nix/sources.nix` already draws that line: it names where a
    working copy of the source goes, and only our own repositories declare
    one.

    A specification that declares no path narrows nothing, and the answer is
    every locked revision. That is a checkout with no nix, or a specification
    that did not evaluate. The second half of the answer says which happened,
    so the caller can say so rather than quietly publishing fifteen refs
    where seven were meant.
    """
    declared = {
        name
        for name, entry in spec.items()
        if isinstance(entry, dict) and entry.get("path")
    }
    if not declared:
        return dict(locked), False
    return {name: rev for name, rev in locked.items() if name in declared}, True


def existing(repo: Repository, ref: str) -> Oid | None:
    """What that ref already points at, or None."""
    found = repo.references.get(ref)
    if found is None:
        return None
    target = found.target
    return target if isinstance(target, Oid) else None


def plan(repo: Repository, locked: dict[str, Oid]) -> tuple[list[Mark], dict[str, Oid]]:
    """The refs to write, and the ones another umbrella commit already holds.

    Every source in the lock gets one. A source with no revision cannot be
    mapped and is left out.
    """
    fresh: list[Mark] = []
    held: dict[str, Oid] = {}
    for source in sorted(locked):
        revision = locked[source]
        ref = name(source, revision)
        already = existing(repo, ref)
        if already is None:
            fresh.append(Mark(source=source, revision=revision, ref=ref))
        else:
            held[source] = already
    return fresh, held
