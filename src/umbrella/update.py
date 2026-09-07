"""Which revision each source should be locked at.

Two arms, and they are the two arms of the resolution they feed.

**A submodule takes the commit the umbrella records.** Not its working copy and
not its branch head. The lock and the pointer are the pair that has to agree,
and the pointer is the one a push already made public. `umbrella status` reports
that pair; this is what answers it.

**Everything else takes the head of the branch the specification names.** So a
run over every name is a re-sync for the submodules and an update for the third
parties.

Nothing here talks to git or to nix. The caller passes in the two things that
do, so the decisions are testable without a network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .model import UmbrellaError

_GITHUB = "https://github.com/"

# What the caller supplies.
HeadOf = Callable[[str, str], str | None]     # url, branch -> revision
Prefetch = Callable[[str, str, str], dict]    # owner, repo, revision -> node


@dataclass(frozen=True)
class Change:
    """One line of the report."""

    name: str
    was: str | None    # the revision the lock held, None when it held none
    now: str | None    # the revision it holds now, None when the name is gone
    where: str         # "pointer", or the branch the revision came from

    @property
    def dropped(self) -> bool:
        return self.now is None


def github_slug(name: str, url: str) -> tuple[str, str]:
    """The owner and the repository, out of a github url.

    A lock node is a github node, and the resolution builds a
    `github:owner/repo/rev` reference from it. A source hosted anywhere else
    cannot be locked this way, and saying so beats writing a node that nothing
    can read.
    """
    if not url.startswith(_GITHUB):
        raise UmbrellaError(f"{name}: {url} is not on github, and a lock node is one")
    slug = url[len(_GITHUB) :].strip("/")
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    owner, _, repo = slug.partition("/")
    if not owner or not repo or "/" in repo:
        raise UmbrellaError(f"{name}: {url} does not name one owner and one repository")
    return owner, repo


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
        owner, repo = github_slug(name, entry["url"])
        revision = pointers.get(name)
        where = "pointer"
        if revision is None:
            branch = entry["branch"]
            revision = head_of(entry["url"], branch)
            where = branch
            if not revision:
                raise UmbrellaError(f"{name}: {entry['url']} has no branch {branch}")
        was = (existing.get(name) or {}).get("rev")
        node = prefetch(owner, repo, revision)
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
