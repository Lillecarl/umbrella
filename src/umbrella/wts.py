"""A wts: a worktreespace.

The name is a hybrid because the thing is. A worktreespace is one more working
copy of a project, made with whichever mechanism that project's mode calls for:
a git worktree under git, a jj workspace under jj. Both share the storage of the
checkout they came from, so neither copies a repository.

For a single project that is one more working copy. For an umbrella it is the
whole constellation: one more working copy of the umbrella plus one per source,
all at the revisions the lock names. Either way it shares storage with the
checkout it came from, so making one is cheap and making ten is fine.

The umbrella's own extra copy follows the umbrella, not the mode: a git
worktree of a git umbrella, a jj workspace of a colocated one.

Only an umbrella worktreespace carries this marker, because only it has a land to
refuse. It goes in the marker directory `Umbrella.markers` picks, which is `.jj`
for a workspace, because a workspace has no `.git` of its own.

An umbrella worktreespace does not publish, and land refuses in one.

In jj mode it could not anyway: the extra working copies are workspaces, and a
workspace does not move git HEAD, so the umbrella there has nothing to record.
In git mode a submodule worktree does have its own HEAD, so publishing would
technically work. It is still refused, because behaving differently by mode is
worse than never publishing.
"""

from __future__ import annotations

from pathlib import Path

MARKER = "umbrella-wts"


def read(markers: Path) -> str | None:
    path = markers / MARKER
    if not path.exists():
        return None
    return path.read_text().strip() or None


def write(markers: Path, name: str) -> None:
    (markers / MARKER).write_text(f"{name}\n")
