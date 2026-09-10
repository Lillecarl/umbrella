"""The umbrella repo and the sources it locks, read through libgit2."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

import pygit2
from pygit2 import Oid, Repository

from .errors import UmbrellaError
from .kind import Kind
from pygit2.enums import RepositoryOpenFlag

#: How many hex digits of a commit id to print.
#:
#: Git abbreviates to seven by default and grows the number when seven
#: is not unique. Eight is one more than that, which is plenty for a
#: collection of a handful of repositories. Every message that shows an
#: id uses this one, so two lines line up.
SHORT_ID = 8

__all__ = ["SHORT_ID", "Relation", "Source", "Umbrella", "UmbrellaError"]


class Relation(enum.StrEnum):
    """How a working copy relates to the revision the lock names."""

    SAME = "in-sync"
    AHEAD = "ahead-of-lock"
    BEHIND = "behind-lock"
    DIVERGED = "diverged-from-lock"
    UNLOCKED = "not-in-the-lock"
    MISSING = "locked-commit-not-in-checkout"


@dataclass(frozen=True)
class Source:
    """One project the umbrella locks.

    A source has a working copy or it does not, and either is normal. With one,
    `nix/resolve.nix` reads that checkout at the locked revision and this tool
    can land new work from it. Without one, the lock is the whole answer and
    nothing needs to be on disk.

    The working copy is an ordinary clone. The umbrella ignores the directory
    and records nothing about it, so it can be a jj repo, a git repo, or a
    checkout of a branch nobody else has.
    """

    name: str
    url: str
    workdir: Path
    locked: Oid | None

    @property
    def colocated(self) -> bool:
        return (self.workdir / ".jj").is_dir()

    @property
    def present(self) -> bool:
        """Is there a working copy here at all?

        A jj repo made with `jj git init` and no --colocate has no .git, so
        .git alone is not the question.
        """
        return (self.workdir / ".git").exists() or (self.workdir / ".jj").is_dir()

    def repo(self) -> Repository:
        # NO_SEARCH matters. Without it an empty directory resolves upward to
        # the umbrella, and every question about the source then gets the
        # umbrella's answer.
        return Repository(str(self.workdir), RepositoryOpenFlag.NO_SEARCH)

    def head(self) -> Oid | None:
        repo = self.repo()
        return None if repo.head_is_unborn else repo.head.target

    def contains(self, oid: Oid) -> bool:
        return oid in self.repo()

    def relation(self) -> Relation:
        if self.locked is None:
            return Relation.UNLOCKED
        head = self.head()
        if head is None:
            return Relation.MISSING
        if head == self.locked:
            return Relation.SAME
        repo = self.repo()
        if self.locked not in repo:
            return Relation.MISSING
        if repo.descendant_of(head, self.locked):
            return Relation.AHEAD
        if repo.descendant_of(self.locked, head):
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
    """A repo holding a Nix lock, and the working copies beside it.

    The repo itself can be plain git or a colocated jj repo. Nothing here
    depends on which, because the umbrella records nothing but a text file.
    """

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.workdir = Path(repo.workdir)

    @classmethod
    def open(cls, start: Path | None = None) -> "Umbrella":
        found = pygit2.discover_repository(str(start or Path.cwd()))
        if found is None:
            raise UmbrellaError(
                "not inside a git repo. A jj repo works too, if it is colocated: "
                "jj git init --colocate."
            )
        repo = Repository(found)
        if repo.is_bare or repo.workdir is None:
            raise UmbrellaError("the umbrella needs a working copy")
        return cls(repo)

    @property
    def kind(self) -> Kind:
        from . import kind as kind_module

        return kind_module.read(self.repo)

    def workdir_of(self, name: str) -> Path:
        """Where a source's working copy goes.

        One directory beside the umbrella, named after the source. The
        specification says the same thing in its `path` field, and `update`
        checks that the two agree. One convention, checked in one place.
        """
        return self.workdir / name

    # -- the lock ---------------------------------------------------------

    def lock_entries(self, revision: str | None = None) -> dict[str, dict]:
        """The lock nodes, from the working copy or from a commit."""
        from . import lock

        if revision is None:
            return lock.entries(self.workdir)
        blob = self._blob_at(self.tree_at(revision), lock.PATH)
        if blob is None:
            return {}
        return lock.parse(blob.data.decode(), f"{lock.PATH} at {revision}")

    def sources(self, revision: str | None = None) -> list[Source]:
        """Every source the lock names, sorted by name.

        Empty for a single project, which locks nothing and coordinates
        nobody.
        """
        from . import lock

        if self.kind is not Kind.UMBRELLA:
            return []
        out = []
        for name, entry in self.lock_entries(revision).items():
            try:
                url = lock.url(name, entry)
            except UmbrellaError:
                # A node this tool cannot clone from is still a node. It shows
                # in `status` and it locks a revision; only `fetch` needs a url.
                url = ""
            out.append(
                Source(
                    name=name,
                    url=url,
                    workdir=self.workdir_of(name),
                    locked=lock.revision(entry),
                )
            )
        return sorted(out, key=lambda s: s.name)

    def source(self, name: str) -> Source | None:
        return next((s for s in self.sources() if s.name == name), None)

    # -- history ----------------------------------------------------------

    def head_tree(self) -> pygit2.Tree | None:
        if self.repo.head_is_unborn:
            return None
        return self.repo.revparse_single("HEAD").tree

    def tree_at(self, revision: str) -> pygit2.Tree:
        try:
            return self.repo.revparse_single(revision).peel(pygit2.Commit).tree
        except (KeyError, pygit2.GitError, ValueError) as error:
            raise UmbrellaError(f"no such revision: {revision} ({error})") from error

    def _blob_at(self, tree: pygit2.Tree | None, path: str) -> pygit2.Blob | None:
        if tree is None:
            return None
        try:
            entry = self.repo[tree[path].id]
        except KeyError:
            return None
        return entry if isinstance(entry, pygit2.Blob) else None

    def committed_lock(self) -> dict[str, dict]:
        """The lock as HEAD holds it. Empty before the first commit."""
        if self.repo.head_is_unborn:
            return {}
        return self.lock_entries("HEAD")

    def staged_lock(self) -> dict[str, dict] | None:
        """The lock as the index holds it, or None when it is not staged.

        The pre-commit guard asks this. A lock the working copy has edited but
        nobody staged is not about to be committed, so it is not its business.
        """
        from . import lock

        index = self.repo.index
        index.read()
        try:
            entry = index[lock.PATH]
        except KeyError:
            return None
        blob = self.repo[entry.id]
        if not isinstance(blob, pygit2.Blob):
            return None
        return lock.parse(blob.data.decode(), f"{lock.PATH} (staged)")

    def commits_in_range(self, tip: Oid, hide: list[Oid]) -> list[pygit2.Commit]:
        walker = self.repo.walk(tip)
        for oid in hide:
            if oid in self.repo:
                walker.hide(oid)
        return list(walker)

    def locks_introduced(self, commits: list[pygit2.Commit]) -> set[tuple[str, Oid]]:
        """Every revision the given commits newly lock, by source name.

        A revision a commit inherits unchanged is already on the remote,
        because an earlier push checked it. Only the changes are new public
        state.
        """
        from . import lock

        found: set[tuple[str, Oid]] = set()
        for commit in commits:
            parent = commit.parents[0] if commit.parents else None
            here = self._lock_revisions(commit.tree)
            before = self._lock_revisions(parent.tree if parent else None)
            for name, oid in here.items():
                if before.get(name) != oid:
                    found.add((name, oid))
        return found

    def _lock_revisions(self, tree: pygit2.Tree | None) -> dict[str, Oid]:
        from . import lock

        blob = self._blob_at(tree, lock.PATH)
        if blob is None:
            return {}
        try:
            return lock.revisions(lock.parse(blob.data.decode()))
        except UmbrellaError:
            # An unreadable lock somewhere in history is not this push's fault.
            return {}

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
