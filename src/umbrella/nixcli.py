"""The two nix commands that writing a lock needs.

Everything else here goes through git and pygit2. This shells out because the
specification is a Nix file: only nix can read one, and a second parser for a
language that already has an evaluator would be a second answer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SPEC = "nix/sources.nix"

# url and branch, and nothing else. `path` in the specification is a Nix path,
# and asking for it would copy every working copy into the store to answer.
_APPLY = "builtins.mapAttrs (_: v: { inherit (v) url branch; })"


class NixError(RuntimeError):
    """A nix command failed."""


def _capture(cwd: Path, *args: str) -> str:
    argv = ["nix", *args]
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise NixError(f"{' '.join(argv)}\n{proc.stderr.strip()}")
    return proc.stdout


def spec(workdir: Path) -> dict[str, dict[str, str]]:
    """What the specification declares: a url and a branch for each name.

    No --impure. The apply above reads two strings, so a specification that
    needs an impure evaluation to answer this is one that broke.
    """
    if not (workdir / SPEC).is_file():
        raise NixError(f"there is no {SPEC} here, so there is nothing to update")
    return json.loads(
        _capture(workdir, "eval", "--json", "--file", SPEC, "--apply", _APPLY)
    )


def parse_prefetch(raw: str) -> dict:
    """The node a lock records, out of what `nix flake prefetch --json` prints.

    nix puts the hash in two places and not always in both: `locked.narHash`
    when the reference already carried one, and the top level `hash`
    otherwise. A lock node holds one field, so this picks.
    """
    data = json.loads(raw)
    locked = data.get("locked") or {}
    node = {
        key: locked[key]
        for key in ("type", "owner", "repo", "rev", "lastModified")
        if key in locked
    }
    found = locked.get("narHash") or data.get("hash")
    if found is None:
        raise NixError("nix flake prefetch printed no hash")
    node["narHash"] = found
    return node


def prefetch(workdir: Path, owner: str, repo: str, rev: str) -> dict:
    """Fetch one revision, and return the node the lock records for it.

    --refresh, because a reference this program built a moment ago from a
    branch head should not be answered out of a cache of the same reference.
    """
    reference = f"github:{owner}/{repo}/{rev}"
    return parse_prefetch(
        _capture(workdir, "flake", "prefetch", "--json", "--refresh", reference)
    )
