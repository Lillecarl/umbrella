"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from pygit2 import Oid

from . import backend as backends
from . import gitcli, guards, hooks, initcc, jj, kind, lock, mode, nixcli, refs, update, wts
from .backend import Backend
from .kind import Kind
from .mode import Mode
from .model import SHORT_ID, Relation, Sub, Umbrella, UmbrellaError


#: How wide the column that leads a line is, in characters.
#:
#: `PATH` holds a submodule path and `NAME` a worktree or workspace
#: name. Neither one truncates: an f-string pads and never cuts, so a
#: path longer than the column simply pushes the rest of the line
#: right. `prompt-toolkit` is fourteen and already does that, which is
#: why `status` measures its own width instead of using these.
PATH_COLUMN = 12
NAME_COLUMN = 16

#: How wide the HEAD column of `status` is: an abbreviated id and the
#: two spaces that keep it off the next word.
HEAD_COLUMN = SHORT_ID + 2


def _die(message: str) -> None:
    print(f"umbrella: {message}", file=sys.stderr)
    raise SystemExit(1)


def _open() -> tuple[Umbrella, Backend]:
    try:
        umbrella = Umbrella.open()
    except UmbrellaError as error:
        _die(str(error))
        raise
    return umbrella, backends.for_mode(mode.read(umbrella.repo))


def _hints(backend: Backend, subs: list[Sub]) -> None:
    print("\n  Push the submodule first:", file=sys.stderr)
    for sub in subs:
        print(f"    {backend.hint(sub)}", file=sys.stderr)
    print("  Or let umbrella do it:  umbrella land", file=sys.stderr)


# -- setup ----------------------------------------------------------------


def _clone_missing(umbrella: Umbrella) -> None:
    """Check out only the submodules that are not there yet."""
    missing = [s.path for s in umbrella.subs() if not s.present]
    gitcli.submodule_clone(umbrella.workdir, missing)
    for path in missing:
        print(f"{path:<{PATH_COLUMN}} checked out")


def _install(umbrella: Umbrella) -> None:
    """Wire up the guards, but never at the cost of someone else's hooks."""
    if umbrella.kind is not Kind.UMBRELLA:
        # The guards only ever look at submodule pointers. Taking over
        # core.hooksPath to install two hooks that can do nothing would
        # disable whatever hooks this repo already has.
        print("hooks:  none, because a single project has no pointers to guard")
        return

    try:
        existing = umbrella.repo.config["core.hooksPath"]
    except KeyError:
        existing = None
    if existing is not None and existing != hooks.HOOKS_DIR:
        print(f"hooks:  left alone, because core.hooksPath is already {existing}")
        print(f"        to guard this repo, call umbrella from {existing}:")
        print("          umbrella check-commit   in pre-commit")
        print('          umbrella check-push "$@"   in pre-push')
        return

    umbrella.repo.config["core.hooksPath"] = hooks.HOOKS_DIR
    installed = hooks.install(umbrella.repo)
    print(f"config: core.hooksPath={hooks.HOOKS_DIR}")
    print(f"hooks:  {', '.join(installed)}")


def cmd_initgit(umbrella: Umbrella, _backend: Backend, _args) -> int:
    """Check out any missing submodule and install the hooks."""
    _clone_missing(umbrella)
    _install(umbrella)
    print(f"mode:   {mode.read(umbrella.repo)}")
    return 0


def cmd_initjj(umbrella: Umbrella, _backend: Backend, _args) -> int:
    """Colocate the submodules and switch this checkout to jj mode."""
    if shutil.which("jj") is None:
        _die("jj is not on PATH")
    _clone_missing(umbrella)
    _install(umbrella)

    if umbrella.kind is Kind.UMBRELLA:
        # Keep ordinary git commands out of the jj working copies. git checkout
        # inside a submodule fights jj. umbrella sync uses jj new instead.
        umbrella.repo.config["submodule.recurse"] = False
        print("config: submodule.recurse=false")

    if umbrella.kind is not Kind.UMBRELLA:
        if (umbrella.workdir / ".jj").is_dir():
            print("this repo is already colocated")
        else:
            jj.init_colocate(umbrella.workdir)
            print("colocated this repo")
        for bookmark in jj.untracked_remote_bookmarks(umbrella.workdir):
            jj.track(umbrella.workdir, bookmark)
            print(f"tracking {bookmark}")

    for sub in umbrella.subs():
        if sub.colocated:
            print(f"{sub.path:<{PATH_COLUMN}} already colocated")
        else:
            jj.init_colocate(sub.workdir)
            print(f"{sub.path:<{PATH_COLUMN}} colocated")
        for bookmark in jj.untracked_remote_bookmarks(sub.workdir):
            jj.track(sub.workdir, bookmark)
            print(f"{sub.path:<{PATH_COLUMN}} tracking {bookmark}")

    mode.write(umbrella.repo, Mode.JJ)
    print(f"mode:   {Mode.JJ} (marker in .git, never committed)")
    return 0


def cmd_mode(umbrella: Umbrella, _backend: Backend, args) -> int:
    if args.value is None:
        print(mode.read(umbrella.repo))
        return 0
    chosen = Mode(args.value)
    if chosen is Mode.JJ:
        missing = [s.path for s in umbrella.subs() if not s.colocated]
        if missing:
            _die(f"not colocated yet: {', '.join(missing)}. Run: umbrella initjj")
    mode.write(umbrella.repo, chosen)
    print(f"mode: {chosen}")
    return 0


# -- extra working copies -------------------------------------------------


def _to_stderr(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def _wts_path(umbrella: Umbrella, name: str, given: str | None) -> Path:
    if given:
        return Path(given).resolve()
    return umbrella.workdir.parent / f"{umbrella.workdir.name}-{name}"


def _create(
    umbrella: Umbrella,
    backend: Backend,
    name: str,
    path: Path,
    revision: str | None,
    log,
) -> None:
    """Make one extra working copy of whatever this repo is."""
    if umbrella.kind is not Kind.UMBRELLA:
        base = backend.base_revision(umbrella.workdir, revision)
        branch = f"worktree/{name}" if backend.mode is Mode.GIT else None
        backend.add_working_copy(umbrella.workdir, path, name, base, branch)
        log(f"{name:<{NAME_COLUMN}} {path}")
        return

    # The umbrella itself is plain git whichever mode drives the submodules.
    gitcli.worktree_add(umbrella.workdir, path, f"worktree/{name}", revision)
    made = Umbrella.open(path)
    wts.write(made.repo, name)
    mode.write(made.repo, backend.mode)
    kind.write(made.repo, Kind.UMBRELLA)
    hooks.install(made.repo)

    # The pointers come from the commit being checked out, not from HEAD, so
    # a worktreespace of an older umbrella gets the submodules of that day.
    for sub in umbrella.subs(revision):
        if sub.recorded is None or not sub.present:
            log(f"{sub.path:<{PATH_COLUMN}} skipped, nothing recorded to check out")
            continue
        backend.add_working_copy(
            sub.workdir, path / sub.path, name, str(sub.recorded)
        )
        log(f"{sub.path:<{PATH_COLUMN}} at {str(sub.recorded)[:SHORT_ID]}")


def _destroy(umbrella: Umbrella, backend: Backend, name: str, path: Path) -> None:
    if umbrella.kind is Kind.UMBRELLA:
        for sub in umbrella.subs():
            if not sub.present:
                continue
            try:
                backend.drop_working_copy(sub.workdir, path / sub.path, name)
            except (jj.JjError, gitcli.GitError) as error:
                # Removing what is left matters more than one already gone.
                _to_stderr(f"{sub.path:<{PATH_COLUMN}} {str(error).splitlines()[0]}")
        gitcli.worktree_remove(umbrella.workdir, path)
        left = gitcli.drop_branch(umbrella.workdir, f"worktree/{name}")
        if left is not None:
            _to_stderr(
                f"worktree/{name} held {str(left)[:SHORT_ID]}, which is on no other "
                "branch. It is still in the reflog."
            )
    else:
        backend.drop_working_copy(umbrella.workdir, path, name)
    shutil.rmtree(path, ignore_errors=True)


def cmd_wts_add(umbrella: Umbrella, backend: Backend, args) -> int:
    if wts.read(umbrella.repo) is not None:
        _die("this is already a worktreespace. Make the next one from the checkout it came from.")
    path = _wts_path(umbrella, args.name, args.path)
    if path.exists():
        _die(f"{path} already exists")

    _create(umbrella, backend, args.name, path, args.revision, print)
    print(f"wts {args.name} is at {path}")
    if umbrella.kind is Kind.UMBRELLA:
        print("It cannot publish. Land from the checkout it came from.")
    return 0


def cmd_wts_list(umbrella: Umbrella, _backend: Backend, _args) -> int:
    for path in gitcli.worktree_paths(umbrella.workdir):
        if path == umbrella.workdir:
            continue
        try:
            other = Umbrella.open(path)
        except UmbrellaError:
            continue
        name = wts.read(other.repo)
        print(f"{name or '-':<{NAME_COLUMN}} {path}")
    for name in backend_workspaces(umbrella, _backend):
        print(f"{name:<{NAME_COLUMN}} (workspace)")
    return 0


def backend_workspaces(umbrella: Umbrella, backend: Backend) -> list[str]:
    """Workspace names of a single jj project, which git knows nothing about."""
    if umbrella.kind is Kind.UMBRELLA or backend.mode is not Mode.JJ:
        return []
    return [n for n in jj.workspaces(umbrella.workdir) if n != "default"]


def cmd_wts_rm(umbrella: Umbrella, backend: Backend, args) -> int:
    if wts.read(umbrella.repo) is not None:
        _die("run this from the checkout the worktreespace came from, not inside it.")
    path = _wts_path(umbrella, args.name, args.path)
    _destroy(umbrella, backend, args.name, path)
    print(f"wts {args.name} removed")
    return 0


# -- the Claude worktree hooks --------------------------------------------


def _project_dir(umbrella: Umbrella) -> Path:
    given = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(given).resolve() if given else umbrella.workdir


def _hook_root(umbrella: Umbrella) -> Path:
    return _project_dir(umbrella) / ".claude" / "worktrees"


def _hook_name() -> str:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as error:
        _die(f"the hook payload is not json: {error}")
        raise
    name = payload.get("name")
    if not name:
        _die("the hook payload has no name")
    return str(name)


def cmd_hook_create(umbrella: Umbrella, backend: Backend, _args) -> int:
    """Claude's WorktreeCreate hook. Only the path goes to stdout."""
    name = _hook_name()
    root = _hook_root(umbrella)
    root.mkdir(parents=True, exist_ok=True)
    hooks.exclude(umbrella.repo, "/.claude/worktrees/")
    path = root / name
    if path.exists():
        _die(f"{path} already exists")
    _create(umbrella, backend, name, path, None, _to_stderr)
    print(path)
    return 0


def cmd_hook_remove(umbrella: Umbrella, backend: Backend, _args) -> int:
    """Claude's WorktreeRemove hook."""
    name = _hook_name()
    _destroy(umbrella, backend, name, _hook_root(umbrella) / name)
    return 0


def cmd_initcc(umbrella: Umbrella, _backend: Backend, args) -> int:
    """Point Claude's worktree hooks at this program, for this project."""
    name = "settings.local.json" if args.local else "settings.json"
    path = umbrella.workdir / ".claude" / name
    disable = args.disable_plugin or []
    base = Path(args.skills_dir) if args.skills_dir else None
    keep = initcc.discovered_plugins(base) if disable else []
    try:
        done = initcc.apply(
            path, disable, keep, args.run_command or initcc.DEFAULT_COMMAND
        )
    except (ValueError, OSError) as error:
        _die(str(error))
        raise

    for event in done.added:
        print(f"added   {event}")
    for plugin in done.disabled:
        print(f"disabled {plugin} for this project")
    for plugin in done.kept:
        print(f"named    {plugin} as still enabled")
    for item in done.already:
        print(f"kept     {item}, already set")
    print(f"wrote    {path}")

    if done.disabled:
        print()
        print("Hooks merge across settings files and all of them run, so a")
        print("project hook cannot replace a user one. Disabling the plugin is")
        print("what stops two hooks each making a worktree.")
    return 0


# -- kind -----------------------------------------------------------------


def cmd_kind(umbrella: Umbrella, _backend: Backend, args) -> int:
    if args.value is None:
        print(umbrella.kind)
        return 0
    kind.write(umbrella.repo, Kind(args.value))
    print(f"kind: {args.value}")
    return 0


# -- daily ----------------------------------------------------------------


def _worktreespace_note(umbrella: Umbrella) -> str | None:
    """A worktreespace cannot answer questions about submodule pointers.

    In jj mode its submodules are workspaces with no git of their own, so there
    is no HEAD to compare against what the umbrella records. Saying "not
    checked out" there would be wrong, and advising an init would send someone
    to run git submodule update over live workspaces.
    """
    name = wts.read(umbrella.repo)
    if name is None:
        return None
    return (
        f"this is the worktreespace {name}. Submodule pointers are not tracked "
        "here. Work in the submodules directly, and use the checkout it came "
        "from to see or change what the umbrella records."
    )


def _lock_note(locked: dict[str, Oid], sub: Sub) -> str | None:
    """What the lock names, when that is not what the umbrella records.

    A name the lock does not carry is not a fault: most sources in a lock are
    not submodules at all. `Sub.source` is the rule that matches the two, and
    `update` uses the same one.

    This says which commit the lock names and does not say which of the two is
    wrong. Either can be: a `land` that moved the pointer leaves the lock
    behind, and a lock written from a newer revision runs ahead of it.
    """
    rev = locked.get(sub.source)
    if rev is None or rev == sub.recorded:
        return None
    return f"lock-names-{str(rev)[:8]}"


def cmd_status(umbrella: Umbrella, backend: Backend, args) -> int:
    note = _worktreespace_note(umbrella)
    if note is not None:
        print(f"mode: {backend.mode}  kind: {umbrella.kind}")
        print(note)
        return 0
    if args.fetch:
        for sub in umbrella.subs():
            if sub.present:
                backend.fetch(sub)
    print(f"mode: {backend.mode}  kind: {umbrella.kind}")
    if umbrella.kind is not Kind.UMBRELLA:
        print("a single project, so there are no submodule pointers to track")
        for name in backend_workspaces(umbrella, backend):
            print(f"  wts {name}")
        return 0
    subs = umbrella.subs()
    # The lock file is read once, and every row below asks it the same
    # question. It needs no checkout, so it answers for a submodule that is
    # not there -- which is the whole of a pinned checkout, where the lock is
    # the only thing that decides anything.
    locked = lock.read(umbrella.workdir)
    drifted = False
    # A name wider than the column would push every later field out of line.
    width = max([len("SUBMODULE")] + [len(sub.path) for sub in subs])
    print(f"{'SUBMODULE':<{width}} {'HEAD':<{HEAD_COLUMN}} STATE")
    for sub in subs:
        stale = _lock_note(locked, sub)
        drifted = drifted or stale is not None
        trailer = f" {stale}" if stale else ""
        if not sub.present:
            print(
                f"{sub.path:<{width}} {'-':<{HEAD_COLUMN}} not checked out "
                f"(run: umbrella initgit){trailer}"
            )
            continue
        if backend.mode is Mode.JJ and not sub.colocated:
            print(
                f"{sub.path:<{width}} {'-':<{HEAD_COLUMN}} not colocated "
                f"(run: umbrella initjj){trailer}"
            )
            continue
        head = sub.head()
        notes = []
        if backend.conflicted(sub):
            notes.append("CONFLICT")
        if backend.dirty(sub):
            notes.append("uncommitted-work")
        relation = sub.relation()
        if relation is not Relation.SAME:
            notes.append(str(relation))
        if head is not None and not sub.on_remote(head):
            notes.append("not-pushed")
        if head is not None:
            moved = refs.remote_ahead(sub, head)
            if moved is not None:
                notes.append(f"{moved}-moved-ahead")
        if stale is not None:
            notes.append(stale)
        short = str(head)[:SHORT_ID] if head else "-"
        print(f"{sub.path:<{width}} {short:<{HEAD_COLUMN}} {' '.join(notes) or 'in sync'}")
        if head is not None:
            for name in backend.elsewhere(sub, head):
                print(f"{'':<{width}} {'':<{HEAD_COLUMN}} workspace {name} holds work this "
                      "checkout cannot see")
    if drifted:
        print(
            f"\n  {lock.PATH} names a different commit than the pointer the "
            "umbrella records.\n"
            "  Write it again from the recorded revisions. Nothing else here "
            "reads it, and\n"
            "  Nix cannot see the pointer, so neither of them reports this on "
            "its own."
        )
    return 0


def cmd_update(umbrella: Umbrella, _backend: Backend, args) -> int:
    """Write the lock again, from the pointers and the branches.

    This is the answer to the `lock-names-<rev>` row of `status`.
    """
    workdir = umbrella.workdir
    declared = nixcli.spec(workdir)
    # Only a recorded pointer counts. A submodule's own HEAD is not public
    # until a land pushes it and records it, and a lock that named it would
    # send everybody else to a commit no remote has.
    pointers = {
        sub.source: str(sub.recorded)
        for sub in umbrella.subs()
        if sub.recorded is not None
    }
    sources, changes = update.rewrite(
        spec=declared,
        pointers=pointers,
        existing=lock.entries(workdir),
        head_of=lambda url, branch: gitcli.remote_head(workdir, url, branch),
        prefetch=lambda owner, repo, rev: nixcli.prefetch(workdir, owner, repo, rev),
        names=args.names or None,
    )
    if not changes:
        print("nothing moved")
        return 0
    print(update.report(changes))
    if args.dry_run:
        print(f"\n  --dry-run, so {lock.PATH} is untouched")
        return 0
    written = lock.write(workdir, sources)
    print(f"\n  wrote {written.relative_to(workdir)}")
    return 0


def cmd_sync(umbrella: Umbrella, backend: Backend, _args) -> int:
    note = _worktreespace_note(umbrella)
    if note is not None:
        _die(note)
    if umbrella.kind is not Kind.UMBRELLA:
        _die("this is a single project. There are no submodule pointers to sync.")
    for sub in umbrella.subs():
        if not sub.present:
            _die(f"{sub.path} is not checked out. Run: umbrella initgit")
        backend.fetch(sub)
        if sub.recorded is None:
            print(f"{sub.path:<{PATH_COLUMN}} the umbrella records no commit yet")
            continue
        if sub.head() == sub.recorded:
            print(f"{sub.path:<{PATH_COLUMN}} already at {str(sub.recorded)[:SHORT_ID]}")
            continue
        if backend.dirty(sub):
            print(f"{sub.path:<{PATH_COLUMN}} has uncommitted work, so it was left alone")
            continue
        if not sub.contains(sub.recorded):
            _die(
                f"{sub.path}: the umbrella records {sub.recorded}, which no remote "
                "has. Whoever recorded it never pushed it."
            )
        backend.move_to(sub, sub.recorded)
        print(f"{sub.path:<{PATH_COLUMN}} moved to {str(sub.recorded)[:SHORT_ID]}")
    return 0


def _lock_after_land(umbrella: Umbrella, landed: dict[str, Oid]) -> str | None:
    """What the lock still names, once land has moved a pointer.

    land publishes a commit and records it. It does not touch the lock, and
    nothing else will either. `status` reports the pair afterwards; saying it
    here saves the round trip, and names the exact command that ends it.

    A lock nobody can read is not worth failing over at this point. The push
    already happened, so an exit code here would say the wrong thing.
    """
    try:
        locked = lock.read(umbrella.workdir)
    except UmbrellaError as error:
        return f"\n  {error}"
    stale = sorted(
        name for name, oid in landed.items() if name in locked and locked[name] != oid
    )
    if not stale:
        return None
    return (
        f"\n  {lock.PATH} still names another commit for {', '.join(stale)}.\n"
        f"  Run: umbrella update {' '.join(stale)}"
    )


def cmd_land(umbrella: Umbrella, backend: Backend, args) -> int:
    if umbrella.kind is not Kind.UMBRELLA:
        _die("this is a single project. There are no submodule pointers to land.")
    name = wts.read(umbrella.repo)
    if name is not None:
        _die(
            f"this is the worktreespace {name}, which is for throwaway work, so it "
            "does not publish. Land from the checkout it came from."
        )
    if args.push and not args.message:
        _die("land: --push needs -m, because it pushes the commit it makes")

    landed: dict[str, Oid] = {}
    for sub in umbrella.subs():
        if not sub.present:
            _die(f"{sub.path} is not checked out. Run: umbrella initgit")
        if not args.no_advance and backend.finalize(sub):
            print(f"{sub.path:<{PATH_COLUMN}} closed the working commit")
        head = sub.head()
        if head is None or head == sub.recorded:
            if head is not None and backend.dirty(sub):
                print(f"{sub.path:<{PATH_COLUMN}} has work with no description, which land ignores")
            continue
        if backend.conflicted(sub):
            _die(f"{sub.path}: has unresolved conflicts. Resolve them first.")
        if backend.dirty(sub):
            _die(f"{sub.path}: has uncommitted work. Commit it first.")

        try:
            choice = refs.choose(sub, head, backend.default_branch(sub))
        except refs.NoBranch as error:
            _die(str(error))
            raise

        if choice.needs_move:
            if args.no_advance:
                _die(
                    f"{sub.path}: {choice.name} does not point at the commit to "
                    "land, and --no-advance forbids moving it."
                )
            was = str(choice.target)[:SHORT_ID] if choice.target else "new"
            backend.advance(sub, choice.name, choice.target is not None, head)
            print(f"{sub.path:<{PATH_COLUMN}} {choice.name}: {was} -> {str(head)[:SHORT_ID]} (fast-forward)")
        backend.push(sub, choice.name)
        umbrella.stage_gitlink(sub, head)
        print(f"{sub.path:<{PATH_COLUMN}} pushed {choice.name}, staged {str(head)[:SHORT_ID]}")
        landed[sub.source] = head

    if not landed:
        print("nothing to land")
        return 0
    note = _lock_after_land(umbrella, landed)
    if not args.message:
        print("umbrella: staged. Commit with git commit, or rerun with -m.")
        if note:
            print(note)
        return 0

    umbrella.commit(args.message)
    print("umbrella: committed")
    if args.push:
        gitcli.push(umbrella.workdir)
        print("umbrella: pushed")
    if note:
        print(note)
    return 0


# -- hooks ----------------------------------------------------------------


def cmd_check_commit(umbrella: Umbrella, backend: Backend, _args) -> int:
    problems = guards.check_commit(umbrella)
    for problem in problems:
        print(f"pre-commit: {problem}", file=sys.stderr)
    if problems:
        paths = {p.path for p in problems}
        _hints(backend, [s for s in umbrella.subs() if s.path in paths])
    return 1 if problems else 0


def cmd_check_push(umbrella: Umbrella, backend: Backend, args) -> int:
    problems = guards.check_push(umbrella, backend, args.remote, sys.stdin.read())
    for problem in problems:
        print(f"pre-push: {problem}", file=sys.stderr)
    if problems:
        print(
            "pre-push: pushing the umbrella now would break every clone.",
            file=sys.stderr,
        )
        paths = {p.path for p in problems}
        _hints(backend, [s for s in umbrella.subs() if s.path in paths])
    return 1 if problems else 0


# -- wiring ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="umbrella",
        description=(
            "Drive an umbrella git repo whose submodules are separate projects. "
            "Works with plain git submodules, or with colocated jj repos."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "initgit", aliases=["init"], help="check out submodules and install the hooks"
    )
    sub.add_parser(
        "initjj", aliases=["jjinit"], help="colocate the submodules and use jj here"
    )
    mode_parser = sub.add_parser("mode", help="show or set the mode for this checkout")
    mode_parser.add_argument("value", nargs="?", choices=[m.value for m in Mode])

    status = sub.add_parser(
        "status", help="show what is dirty, ahead, behind or unpushed"
    )
    status.add_argument(
        "-f",
        "--fetch",
        action="store_true",
        help="fetch first, so what it says about the remotes is current",
    )
    sub.add_parser("sync", help="move submodules onto the recorded pointers")

    updater = sub.add_parser(
        "update",
        help="write nix/sources.lock from the pointers and the branches",
        description=(
            "Each submodule is locked at the commit the umbrella records, and "
            "every other source at the head of the branch nix/sources.nix "
            "names. A run with no names fetches every source again and drops "
            "any the specification no longer declares."
        ),
    )
    updater.add_argument(
        "names",
        nargs="*",
        help="only these sources. Everything else is left exactly as it is",
    )
    updater.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="say what would move, and write nothing",
    )

    land = sub.add_parser("land", help="push submodules, then record their pointers")
    land.add_argument("-m", "--message", help="commit the umbrella with this message")
    land.add_argument("-p", "--push", action="store_true", help="push the umbrella too")
    land.add_argument(
        "--no-advance",
        action="store_true",
        help=(
            "change nothing in the submodules. Push and record only what is "
            "already in place, moving no branch and closing no working commit"
        ),
    )

    cc = sub.add_parser(
        "initcc", help="write project settings so Claude uses umbrella for worktrees"
    )
    cc.add_argument(
        "--local",
        action="store_true",
        help="write .claude/settings.local.json instead of the shared file",
    )
    cc.add_argument(
        "--disable-plugin",
        action="append",
        metavar="ID",
        help="turn a plugin off for this project, such as jj-worktrees@skills-dir",
    )
    cc.add_argument(
        "--command",
        # dest matters: the subparsers already own "command".
        dest="run_command",
        metavar="CMD",
        help=(
            "what the hooks should run, if umbrella is not on the PATH Claude "
            "starts with. An absolute path belongs with --local"
        ),
    )
    cc.add_argument(
        "--skills-dir",
        metavar="PATH",
        help="where discovered plugins live, if not ~/.claude/skills",
    )

    kinds = sub.add_parser("kind", help="show or set what this repo is")
    kinds.add_argument("value", nargs="?", choices=[k.value for k in Kind])

    trees = sub.add_parser(
        "wts",
        help=(
            "worktreespace: one more working copy, as a git worktree or a jj "
            "workspace depending on the mode"
        ),
    ).add_subparsers(dest="wts_command", required=True)
    tree_add = trees.add_parser("add", help="make one")
    tree_add.add_argument("name")
    tree_add.add_argument("--path", help="where to put it")
    tree_add.add_argument(
        "-r",
        "--revision",
        help=(
            "where it should start. For an umbrella this also decides which "
            "submodule commits it gets"
        ),
    )
    trees.add_parser("list", help="show them")
    tree_rm = trees.add_parser("rm", help="remove one")
    tree_rm.add_argument("name")
    tree_rm.add_argument("--path", help="where it is, if not the default")

    hook = sub.add_parser(
        "hook", help="entry points for Claude's worktree hooks"
    ).add_subparsers(dest="hook_command", required=True)
    hook.add_parser("worktree-create", help="WorktreeCreate: reads json, prints a path")
    hook.add_parser("worktree-remove", help="WorktreeRemove: reads json")

    sub.add_parser("check-commit", help="pre-commit hook body")
    check_push = sub.add_parser("check-push", help="pre-push hook body")
    check_push.add_argument("remote", help="remote name")
    check_push.add_argument("url", nargs="?", help="remote url, unused")

    return parser


ALIASES = {"init": "initgit", "jjinit": "initjj"}

COMMANDS = {
    "initgit": cmd_initgit,
    "initjj": cmd_initjj,
    "mode": cmd_mode,
    "status": cmd_status,
    "sync": cmd_sync,
    "update": cmd_update,
    "land": cmd_land,
    "initcc": cmd_initcc,
    "kind": cmd_kind,
    "wts.add": cmd_wts_add,
    "wts.list": cmd_wts_list,
    "wts.rm": cmd_wts_rm,
    "hook.worktree-create": cmd_hook_create,
    "hook.worktree-remove": cmd_hook_remove,
    "check-commit": cmd_check_commit,
    "check-push": cmd_check_push,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = ALIASES.get(args.command, args.command)
    if command == "hook":
        project = os.environ.get("CLAUDE_PROJECT_DIR")
        if project:
            os.chdir(project)
    umbrella, backend = _open()
    if command == "wts":
        command = f"wts.{args.wts_command}"
    if command == "hook":
        command = f"hook.{args.hook_command}"
    try:
        return COMMANDS[command](umbrella, backend, args)
    except (jj.JjError, gitcli.GitError, nixcli.NixError, UmbrellaError) as error:
        # Every one of these carries a sentence written for the person running
        # the command. A traceback would hide it.
        _die(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
