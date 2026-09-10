"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import backend as backends
from . import (
    adopt,
    gitcli,
    guards,
    hooks,
    ignore,
    initcc,
    jj,
    kind,
    lock,
    mode,
    nixcli,
    refs,
    update,
    wts,
)
from .backend import Backend
from .kind import Kind
from .mode import Mode
from .model import SHORT_ID, Relation, Source, Umbrella, UmbrellaError


#: How wide the column that leads a line is, in characters.
#:
#: `NAME` holds a source name and `WTS` a worktree or workspace name.
#: Neither one truncates: an f-string pads and never cuts, so a name
#: longer than the column simply pushes the rest of the line right.
#: `tree-sitter-nix-numtide` is twenty-three and already does that,
#: which is why `status` measures its own width instead of using these.
NAME_COLUMN = 12
WTS_COLUMN = 16

#: How wide a revision column of `status` is: an abbreviated id and the
#: two spaces that keep it off the next word.
ID_COLUMN = SHORT_ID + 2


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


def _hints(backend: Backend, sources: list[Source]) -> None:
    print("\n  Push the source first:", file=sys.stderr)
    for source in sources:
        print(f"    {backend.hint(source)}", file=sys.stderr)
    print("  Or let umbrella do it:  umbrella land", file=sys.stderr)


def _needs_umbrella(umbrella: Umbrella, what: str) -> None:
    if umbrella.kind is not Kind.UMBRELLA:
        _die(
            f"this is a single project. It locks no sources, so there is nothing to {what}."
        )


# -- setup ----------------------------------------------------------------


def _install(umbrella: Umbrella) -> None:
    """Wire up the guards, but never at the cost of someone else's hooks."""
    if umbrella.kind is not Kind.UMBRELLA:
        # The guards only ever look at locked revisions. Taking over
        # core.hooksPath to install two hooks that can do nothing would
        # disable whatever hooks this repo already has.
        print("hooks:  none, because a single project locks nothing to guard")
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
    if (umbrella.workdir / ".jj").is_dir():
        print("        jj runs neither hook, so they are feedback for git users only")


def _adopt_old_submodules(umbrella: Umbrella) -> None:
    """Move any repository still living under .git/modules into its clone."""
    for source in umbrella.sources():
        if adopt.adopt(source.workdir):
            print(f"{source.name:<{NAME_COLUMN}} repository moved out of .git/modules")
        adopt.forget_submodule(umbrella.workdir, source.name)


def cmd_init(umbrella: Umbrella, backend: Backend, args) -> int:
    """Set this checkout up: hooks, ignores, and the mode for the sources."""
    chosen = Mode.JJ if args.jj else Mode.GIT
    if chosen is Mode.JJ and shutil.which("jj") is None:
        _die("jj is not on PATH")

    if umbrella.kind is Kind.UMBRELLA:
        _adopt_old_submodules(umbrella)
        names = [source.name for source in umbrella.sources()]
        if ignore.write(umbrella.repo, names):
            print(f"ignore: {ignore.FILE} lists {len(names)} source directories")

    if chosen is Mode.JJ:
        if umbrella.kind is not Kind.UMBRELLA:
            # A single project is its own working copy, so it is the one to
            # colocate. An umbrella has nothing of its own to drive with jj:
            # whether it is a jj repo is the person's own business, and jj git
            # init --colocate is how they say so.
            if (umbrella.workdir / ".jj").is_dir():
                print("this repo is already colocated")
            else:
                jj.init_colocate(umbrella.workdir)
                print("colocated this repo")
            for bookmark in jj.untracked_remote_bookmarks(umbrella.workdir):
                jj.track(umbrella.workdir, bookmark)
                print(f"tracking {bookmark}")
        for source in umbrella.sources():
            if not source.present or source.colocated:
                continue
            jj.init_colocate(source.workdir)
            print(f"{source.name:<{NAME_COLUMN}} colocated")
            for bookmark in jj.untracked_remote_bookmarks(source.workdir):
                jj.track(source.workdir, bookmark)
                print(f"{source.name:<{NAME_COLUMN}} tracking {bookmark}")

    _install(umbrella)
    mode.write(umbrella.repo, chosen)
    print(f"mode:   {chosen} (marker in .git, never committed)")

    missing = [s.name for s in umbrella.sources() if not s.present]
    if missing:
        print(
            f"\n  {len(missing)} sources have no working copy here, and resolve "
            "from the lock.\n"
            "  To work on one:  umbrella fetch <name>"
        )
    return 0


def cmd_mode(umbrella: Umbrella, _backend: Backend, args) -> int:
    if args.value is None:
        print(mode.read(umbrella.repo))
        return 0
    chosen = Mode(args.value)
    if chosen is Mode.JJ:
        missing = [s.name for s in umbrella.sources() if s.present and not s.colocated]
        if missing:
            _die(f"not colocated yet: {', '.join(missing)}. Run: umbrella init --jj")
    mode.write(umbrella.repo, chosen)
    print(f"mode: {chosen}")
    return 0


# -- fetching a working copy ----------------------------------------------


def cmd_fetch(umbrella: Umbrella, backend: Backend, args) -> int:
    """Clone a source into this checkout, at the revision the lock names."""
    _needs_umbrella(umbrella, "fetch")
    sources = {s.name: s for s in umbrella.sources()}
    if args.all:
        wanted = sorted(sources)
    elif args.names:
        wanted = sorted(set(args.names))
    else:
        _die("name a source to fetch, or pass --all")
        raise
    unknown = [name for name in wanted if name not in sources]
    if unknown:
        _die(f"{lock.PATH} names no {', '.join(sorted(unknown))}")

    ignore.write(umbrella.repo, sorted(sources))

    for name in wanted:
        source = sources[name]
        if source.present:
            print(f"{name:<{NAME_COLUMN}} already here")
            continue
        if not source.url:
            print(
                f"{name:<{NAME_COLUMN}} the lock gives no url for it", file=sys.stderr
            )
            continue
        if source.workdir.exists() and any(source.workdir.iterdir()):
            _die(f"{source.workdir} is not empty, and it is not a clone")
        gitcli.clone(umbrella.workdir, source.url, source.workdir)
        if backend.mode is Mode.JJ:
            jj.init_colocate(source.workdir)
            for bookmark in jj.untracked_remote_bookmarks(source.workdir):
                jj.track(source.workdir, bookmark)
        fresh = umbrella.source(name)
        if fresh is not None and fresh.locked is not None:
            backend.move_to(fresh, fresh.locked)
            print(f"{name:<{NAME_COLUMN}} cloned, at {str(fresh.locked)[:SHORT_ID]}")
        else:
            print(f"{name:<{NAME_COLUMN}} cloned")
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
        log(f"{name:<{WTS_COLUMN}} {path}")
        return

    if (umbrella.workdir / ".jj").is_dir():
        # A jj workspace has no .git of its own, and every marker this writes
        # into the new checkout -- the worktreespace name, the mode, the kind --
        # lives in one. Without them `umbrella` inside the new directory would
        # discover this checkout's .git and answer for the wrong tree, which is
        # worse than not making it. `jj workspace add` still works by hand; the
        # sources beside it are ordinary clones and need nothing.
        raise UmbrellaError(
            "this umbrella is a jj repo, and a worktreespace of one cannot hold "
            "the markers umbrella needs. Use jj workspace add."
        )

    # The umbrella is plain git whichever mode drives the sources.
    gitcli.worktree_add(umbrella.workdir, path, f"worktree/{name}", revision)
    made = Umbrella.open(path)
    wts.write(made.repo, name)
    mode.write(made.repo, backend.mode)
    kind.write(made.repo, Kind.UMBRELLA)
    hooks.install(made.repo)

    # The revisions come from the commit being checked out, not from the
    # working copy, so a worktreespace of an older umbrella gets the sources of
    # that day.
    for source in umbrella.sources(revision):
        if source.locked is None or not source.present:
            continue
        backend.add_working_copy(
            source.workdir, path / source.name, name, str(source.locked)
        )
        log(f"{source.name:<{NAME_COLUMN}} at {str(source.locked)[:SHORT_ID]}")


def _destroy(umbrella: Umbrella, backend: Backend, name: str, path: Path) -> None:
    if umbrella.kind is Kind.UMBRELLA:
        for source in umbrella.sources():
            if not source.present:
                continue
            try:
                backend.drop_working_copy(source.workdir, path / source.name, name)
            except (jj.JjError, gitcli.GitError) as error:
                # Removing what is left matters more than one already gone.
                _to_stderr(f"{source.name:<{NAME_COLUMN}} {str(error).splitlines()[0]}")
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
        _die(
            "this is already a worktreespace. Make the next one from the "
            "checkout it came from."
        )
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
        print(f"{name or '-':<{WTS_COLUMN}} {path}")
    for name in backend_workspaces(umbrella, _backend):
        print(f"{name:<{WTS_COLUMN}} (workspace)")
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
    """A worktreespace does not publish, so it does not answer for the lock.

    In jj mode its sources are workspaces with no git of their own, so there is
    no HEAD to compare against what the lock names. Saying "not fetched" there
    would be wrong.
    """
    name = wts.read(umbrella.repo)
    if name is None:
        return None
    return (
        f"this is the worktreespace {name}. The lock is not tracked here. Work "
        "in the sources directly, and use the checkout it came from to see or "
        "change what the umbrella locks."
    )


def cmd_status(umbrella: Umbrella, backend: Backend, args) -> int:
    note = _worktreespace_note(umbrella)
    if note is not None:
        print(f"mode: {backend.mode}  kind: {umbrella.kind}")
        print(note)
        return 0
    print(f"mode: {backend.mode}  kind: {umbrella.kind}")
    if umbrella.kind is not Kind.UMBRELLA:
        print("a single project, so there is no lock to track")
        for name in backend_workspaces(umbrella, backend):
            print(f"  wts {name}")
        return 0

    sources = umbrella.sources()
    if args.fetch:
        for source in sources:
            if source.present:
                backend.fetch(source)

    here = [s for s in sources if s.present]
    away = [s for s in sources if not s.present]
    width = max([len("SOURCE")] + [len(s.name) for s in here])
    print(f"{'SOURCE':<{width}} {'LOCKED':<{ID_COLUMN}} {'HEAD':<{ID_COLUMN}} STATE")
    for source in here:
        locked = str(source.locked)[:SHORT_ID] if source.locked else "-"
        if backend.mode is Mode.JJ and not source.colocated:
            print(
                f"{source.name:<{width}} {locked:<{ID_COLUMN}} {'-':<{ID_COLUMN}} "
                "not colocated (run: umbrella init --jj)"
            )
            continue
        head = source.head()
        notes = []
        if backend.conflicted(source):
            notes.append("CONFLICT")
        if backend.dirty(source):
            notes.append("uncommitted-work")
        relation = source.relation()
        if relation is not Relation.SAME:
            notes.append(str(relation))
        if head is not None and not source.on_remote(head):
            notes.append("not-pushed")
        if head is not None:
            moved = refs.remote_ahead(source, head)
            if moved is not None:
                notes.append(f"{moved}-moved-ahead")
        short = str(head)[:SHORT_ID] if head else "-"
        print(
            f"{source.name:<{width}} {locked:<{ID_COLUMN}} {short:<{ID_COLUMN}} "
            f"{' '.join(notes) or 'in sync'}"
        )
        if head is not None:
            for name in backend.elsewhere(source, head):
                print(
                    f"{'':<{width}} {'':<{ID_COLUMN}} {'':<{ID_COLUMN}} workspace "
                    f"{name} holds work this checkout cannot see"
                )
    if away:
        print(f"\n  {len(away)} more from the lock alone, with no working copy here:")
        print(f"    {', '.join(s.name for s in away)}")
    return 0


def cmd_sync(umbrella: Umbrella, backend: Backend, _args) -> int:
    note = _worktreespace_note(umbrella)
    if note is not None:
        _die(note)
    _needs_umbrella(umbrella, "sync")
    for source in umbrella.sources():
        if not source.present:
            continue
        backend.fetch(source)
        if source.locked is None:
            print(f"{source.name:<{NAME_COLUMN}} the lock names no revision")
            continue
        if source.head() == source.locked:
            print(
                f"{source.name:<{NAME_COLUMN}} already at {str(source.locked)[:SHORT_ID]}"
            )
            continue
        if backend.dirty(source):
            print(
                f"{source.name:<{NAME_COLUMN}} has uncommitted work, so it was left alone"
            )
            continue
        if not source.contains(source.locked):
            _die(
                f"{source.name}: the lock names {source.locked}, which no remote "
                "has. Whoever locked it never pushed it."
            )
        backend.move_to(source, source.locked)
        print(f"{source.name:<{NAME_COLUMN}} moved to {str(source.locked)[:SHORT_ID]}")
    return 0


# -- writing the lock -----------------------------------------------------


def _check_paths(umbrella: Umbrella, spec: dict) -> None:
    """The specification and this tool must agree on where a working copy is.

    `fetch` clones into <umbrella>/<name>, and nix/resolve.nix reads whatever
    `path` names. A specification that says somewhere else would leave the
    clone where Nix never looks, and the build would silently take the lock
    instead. That is the exact class of fault this record was rebuilt to end,
    so it stops the command rather than warning.
    """
    for name, entry in sorted(spec.items()):
        declared = entry.get("path") if isinstance(entry, dict) else None
        if not declared:
            continue
        wanted = umbrella.workdir_of(name)
        if Path(declared).resolve() != wanted.resolve():
            raise UmbrellaError(
                f"{name}: {nixcli.SPEC} puts it at {declared}, and umbrella "
                f"puts it at {wanted}. Move one of them."
            )


def _relock(umbrella: Umbrella, pointers: dict[str, str], names: list[str] | None):
    """Write the lock, and say what moved.

    Both `update` and `land` end here. `update` passes no revisions and every
    name follows the branch the specification declares. `land` passes the
    revisions it just pushed, for the sources it pushed them for.
    """
    workdir = umbrella.workdir
    declared = nixcli.spec(workdir)
    _check_paths(umbrella, declared)
    return update.rewrite(
        spec=declared,
        pointers=pointers,
        existing=lock.entries(workdir),
        head_of=lambda url, branch: gitcli.remote_head(workdir, url, branch),
        prefetch=lambda url, rev: nixcli.prefetch(workdir, url, rev),
        names=names,
    )


def _report(umbrella: Umbrella, sources: dict, changes: list, *, dry_run: bool) -> int:
    if not changes:
        print("nothing moved")
        return 0
    print(update.report(changes))
    if dry_run:
        print(f"\n  --dry-run, so {lock.PATH} is untouched")
        return 0
    written = lock.write(umbrella.workdir, sources)
    print(f"\n  wrote {written.relative_to(umbrella.workdir)}")
    return 0


def cmd_update(umbrella: Umbrella, _backend: Backend, args) -> int:
    """Lock every named source at the head of the branch it declares.

    One rule, and it is the reason this and `land` are two commands. This
    follows the forge. `land` publishes what is on this disk and locks that.
    """
    _needs_umbrella(umbrella, "update")
    sources, changes = _relock(umbrella, pointers={}, names=args.names or None)
    return _report(umbrella, sources, changes, dry_run=args.dry_run)


def _declared_branches(umbrella: Umbrella) -> dict[str, str]:
    spec = nixcli.spec_if_readable(umbrella.workdir)
    return {
        name: entry["branch"]
        for name, entry in spec.items()
        if isinstance(entry, dict) and entry.get("branch")
    }


def cmd_land(umbrella: Umbrella, backend: Backend, args) -> int:
    """Push each working copy, then lock the sources at what was pushed."""
    _needs_umbrella(umbrella, "land")
    name = wts.read(umbrella.repo)
    if name is not None:
        _die(
            f"this is the worktreespace {name}, which is for throwaway work, so "
            "it does not publish. Land from the checkout it came from."
        )

    declared = _declared_branches(umbrella)
    landed: dict[str, str] = {}
    for source in umbrella.sources():
        if not source.present:
            continue
        if not args.no_advance and backend.finalize(source):
            print(f"{source.name:<{NAME_COLUMN}} closed the working commit")
        head = source.head()
        if head is None or head == source.locked:
            if head is not None and backend.dirty(source):
                print(
                    f"{source.name:<{NAME_COLUMN}} has work with no description, "
                    "which land ignores"
                )
            continue
        if backend.conflicted(source):
            _die(f"{source.name}: has unresolved conflicts. Resolve them first.")
        if backend.dirty(source):
            _die(f"{source.name}: has uncommitted work. Commit it first.")

        try:
            choice = refs.choose(
                source, head, backend.default_branch(source), declared.get(source.name)
            )
        except refs.NoBranch as error:
            _die(str(error))
            raise

        if choice.needs_move:
            if args.no_advance:
                _die(
                    f"{source.name}: {choice.name} does not point at the commit to "
                    "land, and --no-advance forbids moving it."
                )
            was = str(choice.target)[:SHORT_ID] if choice.target else "new"
            backend.advance(source, choice.name, choice.target is not None, head)
            print(
                f"{source.name:<{NAME_COLUMN}} {choice.name}: {was} -> "
                f"{str(head)[:SHORT_ID]} (fast-forward)"
            )
        backend.push(source, choice.name)
        print(f"{source.name:<{NAME_COLUMN}} pushed {choice.name}")
        landed[source.name] = str(head)

    if not landed:
        print("nothing to land")
        return 0

    # Only now, and only for these. The revisions are on their remotes, so a
    # prefetch can reach them and a lock naming them is public.
    #
    # There is no --dry-run here on purpose. The push is what makes a revision
    # public and nothing can take it back, so a flag that ran it and then said
    # it had changed nothing would be lying about the only step that matters.
    sources, changes = _relock(umbrella, pointers=landed, names=sorted(landed))
    return _report(umbrella, sources, changes, dry_run=False)


# -- hooks ----------------------------------------------------------------


def cmd_check_commit(umbrella: Umbrella, backend: Backend, _args) -> int:
    problems = guards.check_commit(umbrella)
    for problem in problems:
        print(f"pre-commit: {problem}", file=sys.stderr)
    if problems:
        names = {p.name for p in problems}
        _hints(backend, [s for s in umbrella.sources() if s.name in names])
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
        names = {p.name for p in problems}
        _hints(backend, [s for s in umbrella.sources() if s.name in names])
    return 1 if problems else 0


# -- wiring ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="umbrella",
        description=(
            "Drive a repo whose nix/sources.lock names other projects. A source "
            "resolves from the lock, or from a working copy beside the umbrella "
            "when there is one. Git or jj, either side."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init", aliases=["initgit", "initjj"], help="set this checkout up"
    )
    init.add_argument(
        "--jj",
        action="store_true",
        help="drive the working copies with jj, and colocate the ones that are here",
    )

    fetcher = sub.add_parser(
        "fetch",
        help="clone a source into this checkout, at the revision the lock names",
        description=(
            "A source with no working copy resolves from nix/sources.lock, "
            "which costs a store path and no checkout. That is the default and "
            "it needs nothing. This is how to start working on one: the clone "
            "lands beside the umbrella, .gitignore keeps it out of the "
            "umbrella's history, and nix/resolve.nix reads it from then on."
        ),
    )
    fetcher.add_argument("names", nargs="*", help="source names")
    fetcher.add_argument("--all", action="store_true", help="every source in the lock")

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
    sub.add_parser("sync", help="move working copies onto the locked revisions")

    updater = sub.add_parser(
        "update",
        help="lock every source at the head of the branch it declares",
        description=(
            "nix/sources.nix declares a branch for each source. This follows "
            "them. A run with no names fetches every source again and drops any "
            "the specification no longer declares. Use `land` instead for work "
            "that is on this disk and not on a forge yet."
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

    land = sub.add_parser(
        "land",
        help="push the working copies, then lock the sources at what was pushed",
        description=(
            "This writes nix/sources.lock and stops. Committing the umbrella is "
            "left to you, because git and jj want different commands for it and "
            "picking one here would be wrong in the other."
        ),
    )
    land.add_argument(
        "--no-advance",
        action="store_true",
        help=(
            "change nothing in the working copies. Push and lock only what is "
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
            "source revisions it gets"
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


COMMANDS = {
    "init": cmd_init,
    "fetch": cmd_fetch,
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
    command = args.command
    # The old names still work, and initjj still means the jj one.
    if command in ("initgit", "initjj"):
        args.jj = args.jj or command == "initjj"
        command = "init"
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
