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

# url, branch, and the path when the specification gives one.
#
# `toString` on the path, never interpolation. A Nix path in a string reads
# the directory into the store, so `"${v.path}"` would copy every working copy
# to answer a question about where it is. `toString` returns the location and
# copies nothing.
_APPLY = (
    "builtins.mapAttrs (_: v: "
    "{ inherit (v) url branch; } "
    "// (if v ? path then { path = toString v.path; } else { }))"
)


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


def spec_if_readable(workdir: Path) -> dict[str, dict[str, str]]:
    """The specification, or nothing at all.

    `land` wants the branch each source declares, and `status` wants nothing
    from here. Neither should fail because nix is missing or because the
    specification does not evaluate today: both have an answer that does not
    need it. Only `update` writes the lock, and only `update` insists.
    """
    try:
        return spec(workdir)
    except (NixError, OSError, ValueError):
        return {}


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
        for key in ("type", "owner", "repo", "url", "rev", "lastModified")
        if key in locked
    }
    found = locked.get("narHash") or data.get("hash")
    if found is None:
        raise NixError("nix flake prefetch printed no hash")
    node["narHash"] = found
    return node


def reference(url: str, rev: str) -> str:
    """The flake reference that fetches one revision of one repository.

    A github url becomes a `github:` reference, because that is what
    nix/resolve.nix builds from a github node and the two have to agree.
    Anything else becomes a plain `git+` reference, which nix fetches over the
    same protocols git does, file:// included.
    """
    slug = url
    for prefix in ("https://github.com/", "git@github.com:"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :].strip("/")
            if slug.endswith(".git"):
                slug = slug[: -len(".git")]
            owner, _, repo = slug.partition("/")
            if owner and repo and "/" not in repo:
                return f"github:{owner}/{repo}/{rev}"
            break
    return f"git+{url}?rev={rev}"


def prefetch(workdir: Path, url: str, rev: str) -> dict:
    """Fetch one revision, and return the node the lock records for it.

    --refresh, because a reference this program built a moment ago from a
    branch head should not be answered out of a cache of the same reference.
    """
    return parse_prefetch(
        _capture(
            workdir, "flake", "prefetch", "--json", "--refresh", reference(url, rev)
        )
    )
