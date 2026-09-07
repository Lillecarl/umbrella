"""The umbrella repo and its submodules, read through libgit2."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pygit2
from pygit2 import Oid, Repository

from .kind import Kind
from pygit2.enums import FileMode, RepositoryOpenFlag

#: How many hex digits of a commit id to print.
#:
#: Git abbreviates to seven by default and grows the number when seven
#: is not unique. Eight is one more than that, which is plenty for a
#: collection of a handful of repositories. Every message that shows an
#: id uses this one, so two lines line up.
SHORT_ID = 8


class UmbrellaError(RuntimeError):
    """The umbrella is not usable."""


class Relation(enum.StrEnum):
    """How a submodule checkout relates to the pointer the umbrella records."""

    SAME = "in-sync"
    AHEAD = "ahead-of-umbrella"
    BEHIND = "behind-umbrella"
    DIVERGED = "diverged-from-umbrella"
    NO_POINTER = "no-pointer-recorded"
    POINTER_MISSING = "pointer-not-in-checkout"


def _tree_gitlink(tree: pygit2.Tree | None, path: str) -> Oid | None:
    if tree is None:
        return None
    try:
        entry = tree[path]
    except KeyError:
        return None
    return entry.id if entry.filemode == FileMode.COMMIT else None


@dataclass(frozen=True)
class Sub:
    """One submodule of the umbrella."""

    name: str
    path: str
    url: str
    workdir: Path
    recorded: Oid | None
    declared: str | None   # submodule.<name>.branch from .gitmodules

    @property
    def source(self) -> str:
        """The name a Nix source set gives this submodule.

        A submodule is known by its path and a source by its name. They meet at
        the last component of the path. `status` and `update` both need the
        rule, and two copies of it would let them disagree about which sources
        are submodules at all.
        """
        return PurePosixPath(self.path).name

    @property
    def colocated(self) -> bool:
        return (self.workdir / ".jj").is_dir()

    @property
    def present(self) -> bool:
        """Is there a working copy here at all?

        A clone without --recurse-submodules leaves the directory empty. A jj
        workspace has no .git of its own, so .git alone is not the question.
        """
        return (self.workdir / ".git").exists() or (self.workdir / ".jj").is_dir()

    def repo(self) -> Repository:
        # NO_SEARCH matters. Without it an empty submodule directory resolves
        # upward to the umbrella, and every question about the submodule then
        # gets the umbrella's answer.
        return Repository(str(self.workdir), RepositoryOpenFlag.NO_SEARCH)

    def head(self) -> Oid | None:
        repo = self.repo()
        return None if repo.head_is_unborn else repo.head.target

    def contains(self, oid: Oid) -> bool:
        return oid in self.repo()

    def relation(self) -> Relation:
        if self.recorded is None:
            return Relation.NO_POINTER
        head = self.head()
        if head is None:
            return Relation.POINTER_MISSING
        if head == self.recorded:
            return Relation.SAME
        repo = self.repo()
        if self.recorded not in repo:
            return Relation.POINTER_MISSING
        if repo.descendant_of(head, self.recorded):
            return Relation.AHEAD
        if repo.descendant_of(self.recorded, head):
            return Relation.BEHIND
        return Relation.DIVERGED

    def on_remote(self, oid: Oid) -> bool:
        """Is this commit reachable from any remote-tracking branch?

        descendant_of is strict, so the equal case needs its own arm.
        """
        repo = self.repo()
        if oid not in repo:
            return False
        for name in repo.branches.remote:
            try:
                target = repo.branches.remote[name].target
            except (KeyError, TypeError):
                continue  # a symbolic ref such as origin/HEAD
            if not isinstance(target, Oid):
                continue
            if target == oid or repo.descendant_of(target, oid):
                return True
        return False


class Umbrella:
    """A plain git repo whose submodules are colocated jj repos."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.workdir = Path(repo.workdir)

    @classmethod
    def open(cls, start: Path | None = None) -> "Umbrella":
        found = pygit2.discover_repository(str(start or Path.cwd()))
        if found is None:
            raise UmbrellaError("not inside a git repo")
        repo = Repository(found)
        if repo.is_bare or repo.workdir is None:
            raise UmbrellaError("the umbrella needs a working copy")
        workdir = Path(repo.workdir)
        made = cls(repo)
        # A colocated jj repo is the normal shape for a single project. It is
        # only wrong for an umbrella, which has gitlinks to record and jj
        # ignores those.
        if made.kind is Kind.UMBRELLA and (workdir / ".jj").is_dir():
            raise UmbrellaError(
                "the umbrella is a jj repo. jj ignores gitlinks, so it can never "
                "record a submodule pointer. Remove .jj."
            )
        return made

    def head_tree(self) -> pygit2.Tree | None:
        if self.repo.head_is_unborn:
            return None
        return self.repo.revparse_single("HEAD").tree

    @property
    def kind(self) -> Kind:
        from . import kind as kind_module

        return kind_module.read(self.repo)

    def tree_at(self, revision: str) -> pygit2.Tree:
        try:
            return self.repo.revparse_single(revision).peel(pygit2.Commit).tree
        except (KeyError, pygit2.GitError, ValueError) as error:
            raise UmbrellaError(f"no such revision: {revision} ({error})") from error

    def subs(self, revision: str | None = None) -> list[Sub]:
        """The submodules this umbrella coordinates.

        A revision reads the pointers that commit records, rather than the ones
        checked out now. Empty for a single project, and empty when the kind
        marker says to leave the submodules alone.
        """
        if self.kind is not Kind.UMBRELLA:
            return []
        tree = self.head_tree() if revision is None else self.tree_at(revision)
        out = []
        for sub in self.repo.submodules:
            out.append(
                Sub(
                    name=sub.name,
                    path=sub.path,
                    url=sub.url or "",
                    workdir=self.workdir / sub.path,
                    recorded=_tree_gitlink(tree, sub.path),
                    declared=self._declared_branch(sub.branch),
                )
            )
        return sorted(out, key=lambda s: s.path)

    def _declared_branch(self, declared: str | None) -> str | None:
        """Resolve submodule.<name>.branch from .gitmodules.

        git gives "." a special meaning: use the branch the umbrella itself is
        on. Anything else is a literal branch name.
        """
        if not declared:
            return None
        if declared != ".":
            return declared
        if self.repo.head_is_unborn or self.repo.head_is_detached:
            return None
        return self.repo.head.shorthand

    def sub(self, path: str) -> Sub | None:
        return next((s for s in self.subs() if s.path == path), None)

    # -- index and commit -------------------------------------------------

    def stage_gitlink(self, sub: Sub, oid: Oid) -> None:
        index = self.repo.index
        index.read()
        index.add(pygit2.IndexEntry(sub.path, oid, FileMode.COMMIT))
        index.write()

    def staged_gitlinks(self) -> dict[str, Oid]:
        index = self.repo.index
        index.read()
        return {e.path: e.id for e in index if e.mode == FileMode.COMMIT}

    def commit(self, message: str) -> Oid:
        index = self.repo.index
        index.read()
        tree = index.write_tree()
        sig = self.repo.default_signature
        parents = [] if self.repo.head_is_unborn else [self.repo.head.target]
        return self.repo.create_commit("HEAD", sig, sig, message, tree, parents)

    # -- history ----------------------------------------------------------

    def commits_in_range(self, tip: Oid, hide: list[Oid]) -> list[pygit2.Commit]:
        walker = self.repo.walk(tip)
        for oid in hide:
            if oid in self.repo:
                walker.hide(oid)
        return list(walker)

    def gitlinks_introduced(
        self, commits: list[pygit2.Commit], paths: list[str]
    ) -> set[tuple[str, Oid]]:
        """Every pointer value the given commits introduce.

        A pointer a commit inherits unchanged is already on the remote, because
        an earlier push checked it. Only the changes are new public state.
        """
        found: set[tuple[str, Oid]] = set()
        for commit in commits:
            parent = commit.parents[0] if commit.parents else None
            for path in paths:
                current = _tree_gitlink(commit.tree, path)
                if current is None:
                    continue
                if current != _tree_gitlink(parent.tree if parent else None, path):
                    found.add((path, current))
        return found

    def remote_branch_tips(self, remote: str) -> list[Oid]:
        tips = []
        for name in self.repo.branches.remote:
            if not name.startswith(f"{remote}/"):
                continue
            try:
                target = self.repo.branches.remote[name].target
            except (KeyError, TypeError):
                continue
            if isinstance(target, Oid):
                tips.append(target)
        return tips
