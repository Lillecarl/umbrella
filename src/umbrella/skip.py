"""Which submodules this checkout leaves to the lock.

Nobody hacks on every project at once. A source with no working copy resolves
from `nix/sources.lock` instead, which is a store path and needs no checkout --
the resolution already works that way, one source at a time. What was missing
is a way to tell the tool, which otherwise checks every submodule out again and
calls a missing one a fault.

So this is a list of paths the tool leaves alone. It does not change how
anything resolves: Nix reads a working copy when it finds one, whatever this
file says. It only stops `initgit` cloning it, `sync` and `land` failing over
it, and `status` calling it a mistake.

The marker lives in .git, like the mode and the kind. It is one person's
choice about one checkout, so it is never committed and never shared.

One thing it cannot do. The pre-push hook verifies that every pointer in the
push range is on a remote, and it verifies it by looking in the checkout. A
skipped submodule has none, so a push that carries a new pointer for it is
refused. That is the guarantee working, not a fault: check it out again for as
long as it takes to land it.
"""

from __future__ import annotations

from pathlib import Path

import pygit2

MARKER = "umbrella-skip"


def _marker(repo: pygit2.Repository) -> Path:
    return Path(repo.path) / MARKER


def read(repo: pygit2.Repository) -> set[str]:
    """The submodule paths this checkout leaves to the lock."""
    path = _marker(repo)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def write(repo: pygit2.Repository, paths: set[str]) -> None:
    marker = _marker(repo)
    if not paths:
        marker.unlink(missing_ok=True)
        return
    marker.write_text("".join(f"{path}\n" for path in sorted(paths)))
