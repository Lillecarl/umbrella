"""A wts: a worktreespace.

The name is a hybrid because the thing is. A worktreespace is one more working
copy of a project, made with whichever mechanism that project's mode calls for:
a git worktree under git, a jj workspace under jj. Both share the storage of the
checkout they came from, so neither copies a repository.

For a single project that is one more working copy. For an umbrella it is the
whole constellation: an umbrella worktree plus one working copy per submodule,
all at the commits the umbrella records. Either way it shares storage with the
checkout it came from, so making one is cheap and making ten is fine.

Only an umbrella worktreespace carries this marker, because only it has a land to
refuse.

An umbrella worktreespace does not publish, and land refuses in one.

In jj mode it could not anyway: the extra working copies are workspaces, and a
workspace does not move git HEAD, so the umbrella there has nothing to record.
In git mode a submodule worktree does have its own HEAD, so publishing would
technically work. It is still refused, because behaving differently by mode is
worse than never publishing.
"""

from __future__ import annotations

from pathlib import Path

import pygit2

MARKER = "umbrella-wts"


def _marker(repo: pygit2.Repository) -> Path:
    return Path(repo.path) / MARKER


def read(repo: pygit2.Repository) -> str | None:
    path = _marker(repo)
    if not path.exists():
        return None
    return path.read_text().strip() or None


def write(repo: pygit2.Repository, name: str) -> None:
    _marker(repo).write_text(f"{name}\n")
