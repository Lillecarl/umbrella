"""Which revision each source should be locked at.

Two arms, and each has one caller.

**A revision the caller already decided.** `land` passes what it just pushed,
for the sources it pushed. Those are on their remotes and nothing here has to
find them.

**The head of the branch the specification declares.** Everything else, which
is what `update` asks for.

Nothing here talks to git or to nix. The caller passes in the two things that
do, so the decisions are testable without a network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .errors import UmbrellaError

# What the caller supplies.
HeadOf = Callable[[str, str], str | None]  # url, branch -> revision
Prefetch = Callable[[str, str], dict]  # url, revision -> lock node


@dataclass(frozen=True)
class Change:
    """One line of the report."""

    name: str
    was: str | None  # the revision the lock held, None when it held none
    now: str | None  # the revision it holds now, None when the name is gone
    where: str  # "landed", or the branch the revision came from

    @property
    def dropped(self) -> bool:
        return self.now is None


def rewrite(
    *,
    spec: Mapping[str, Mapping[str, str]],
    pointers: Mapping[str, str],
    existing: Mapping[str, Mapping],
    head_of: HeadOf,
    prefetch: Prefetch,
    names: list[str] | None = None,
) -> tuple[dict[str, dict], list[Change]]:
    """The sources a new lock should hold, and what moved to get there.

    `names` limits the run to those sources and leaves every other node exactly
    as it was. Without it every name is fetched again, and a node the
    specification no longer declares is dropped -- which only a full run may
    do, because a limited one was never told about the rest.
    """
    if names is None:
        wanted = sorted(spec)
        keep = {name: dict(node) for name, node in existing.items() if name in spec}
    else:
        wanted = sorted(set(names))
        unknown = [name for name in wanted if name not in spec]
        if unknown:
            raise UmbrellaError(f"the specification declares no {', '.join(unknown)}")
        keep = {name: dict(node) for name, node in existing.items()}

    changes = [
        Change(name=name, was=(existing[name] or {}).get("rev"), now=None, where="gone")
        for name in sorted(existing)
        if name not in keep
    ]

    for name in wanted:
        entry = spec[name]
        revision = pointers.get(name)
        where = "landed"
        if revision is None:
            branch = entry["branch"]
            revision = head_of(entry["url"], branch)
            where = branch
            if not revision:
                raise UmbrellaError(f"{name}: {entry['url']} has no branch {branch}")
        was = (existing.get(name) or {}).get("rev")
        node = prefetch(entry["url"], revision)
        keep[name] = node
        if node.get("rev") != was:
            changes.append(Change(name=name, was=was, now=node.get("rev"), where=where))

    return keep, changes


def report(changes: list[Change]) -> str:
    """The lines a human reads, aligned on the name."""
    width = max([0] + [len(change.name) for change in changes])
    lines = []
    for change in sorted(changes, key=lambda c: c.name):
        was = (change.was or "")[:8] or "-"
        now = "dropped" if change.dropped else (change.now or "")[:8]
        lines.append(f"{change.name:<{width}}  {was} -> {now}  ({change.where})")
    return "\n".join(lines)
