"""A lab of real repositories, built fresh for every test.

Nothing here is mocked except nix. Each fixture builds bare origins and working
clones on disk, so a test exercises the same git and jj that a user would.

nix is the exception because it cannot be one. `update` and `land` read
nix/sources.nix and prefetch a revision, and neither works in a build sandbox:
there is no network, and recursive nix is off. So `nix_free` answers those two
questions from the lab itself. Every other decision is made against real
repositories, including which revision the lock ends up naming.

The lab locks its sources as `git` nodes over file:// urls. That is a real node
shape -- nix/resolve.nix builds a `git+` reference from one -- and it lets
`fetch` clone for real.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from umbrella import ignore

HAS_JJ = shutil.which("jj") is not None
needs_jj = pytest.mark.skipif(not HAS_JJ, reason="jj is not installed")

SOURCES = ("sub1", "sub2")


def run(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc.stdout


@pytest.fixture(scope="session", autouse=True)
def _sandbox_identity(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Give git and jj an identity and a writable home.

    The nix build sandbox has neither, and both refuse to commit without one.
    """
    home = tmp_path_factory.mktemp("home")

    # libgit2 does not read GIT_CONFIG_GLOBAL, so the file has to sit where it
    # looks, and its search path has to point at this home.
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n"
        "\tname = Umbrella Test\n"
        "\temail = test@example.invalid\n"
        "[init]\n"
        "\tdefaultBranch = main\n"
        '[protocol "file"]\n'
        # The lab uses file:// origins. Real remotes never need this.
        "\tallow = always\n"
        "[advice]\n"
        "\tdetachedHead = false\n"
    )

    jjconfig = home / "jj.toml"
    jjconfig.write_text(
        '[user]\nname = "Umbrella Test"\nemail = "test@example.invalid"\n'
    )

    # git runs the hooks from the checkout with its own environment. A shell
    # shim would have to rebuild PYTHONPATH, and overwriting it loses pygit2.
    # A python shim just adds the package and keeps the interpreter's own path.
    import pygit2
    from pygit2.enums import ConfigLevel

    import umbrella as package

    pygit2.settings.search_path[ConfigLevel.GLOBAL] = str(home)

    package_root = Path(package.__file__).resolve().parent.parent
    shim = home / "umbrella-shim"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(package_root)!r})\n"
        "from umbrella.cli import main\n"
        "raise SystemExit(main(sys.argv[1:]))\n"
    )
    shim.chmod(0o755)

    os.environ.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
        JJ_CONFIG=str(jjconfig),
        UMBRELLA_EXE=str(shim),
        GIT_TERMINAL_PROMPT="0",
    )


def node(url: str, rev: str) -> dict:
    """One lock node, shaped the way `nix flake prefetch` prints a git one."""
    return {
        "type": "git",
        "url": url,
        "rev": rev,
        "narHash": f"sha256-{rev[:6]}",
        "lastModified": 1,
    }


@dataclass
class Lab:
    """Bare origins plus the seed content that was pushed to them."""

    root: Path
    origins: Path

    def origin(self, name: str) -> Path:
        return self.origins / f"{name}.git"

    def url(self, name: str) -> str:
        return f"file://{self.origin(name)}"

    def clone(self, name: str = "work") -> Path:
        """A plain clone of the umbrella. No working copies come with it."""
        dest = self.root / name
        run("git", "clone", "-q", str(self.origin("umbrella")), str(dest))
        return dest

    def push_from_elsewhere(self, source: str, text: str) -> str:
        """Advance a source's main on the origin, behind everyone's back.

        This is how a test creates a stale remote-tracking ref.
        """
        work = self.root / f"_side-{source}-{text}"
        run("git", "clone", "-q", str(self.origin(source)), str(work))
        (work / "file.txt").write_text(text)
        run("git", "commit", "-qam", text, cwd=work)
        run("git", "push", "-q", "origin", "main", cwd=work)
        return run("git", "rev-parse", "HEAD", cwd=work).strip()

    def spec(self) -> dict[str, dict[str, str]]:
        return {name: {"url": self.url(name), "branch": "main"} for name in SOURCES}


def _seed_source(lab: Lab, name: str) -> str:
    origin = lab.origin(name)
    run("git", "init", "--bare", "-q", str(origin))
    work = lab.root / f"_seed-{name}"
    run("git", "init", "-q", "-b", "main", str(work))
    (work / "file.txt").write_text(f"{name} v1\n")
    run("git", "add", ".", cwd=work)
    run("git", "commit", "-qm", f"seed {name}", cwd=work)
    run("git", "remote", "add", "origin", str(origin), cwd=work)
    run("git", "push", "-q", "origin", "main", cwd=work)
    return run("git", "rev-parse", "HEAD", cwd=work).strip()


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    """Two source projects and an umbrella whose lock names them."""
    made = Lab(root=tmp_path, origins=tmp_path / "origins")
    made.origins.mkdir()

    seeded = {name: _seed_source(made, name) for name in SOURCES}

    run("git", "init", "--bare", "-q", str(made.origin("umbrella")))
    seed = tmp_path / "_seed-umbrella"
    run("git", "init", "-q", "-b", "main", str(seed))
    (seed / "nix").mkdir()
    # Never evaluated here: `nix_free` answers for it. It is written so that a
    # checkout looks like the real thing, and so that a person reading a failed
    # test sees what a specification is.
    (seed / "nix" / "sources.nix").write_text(
        "{\n"
        + "".join(
            f'  {name} = {{ url = "{made.url(name)}"; branch = "main"; '
            f"path = ../{name}; }};\n"
            for name in SOURCES
        )
        + "}\n"
    )
    write_lock(seed, {name: node(made.url(name), rev) for name, rev in seeded.items()})
    # A real umbrella commits this, and `init` only keeps it current. Seeding
    # it here is what makes a plain `git pull` work in a checkout that has
    # fetched a working copy.
    ignore.write(seed, list(SOURCES))
    run("git", "add", ".", cwd=seed)
    run("git", "commit", "-qm", "seed umbrella", cwd=seed)
    run("git", "remote", "add", "origin", str(made.origin("umbrella")), cwd=seed)
    run("git", "push", "-q", "origin", "main", cwd=seed)
    return made


def write_lock(workdir: Path, sources: dict[str, dict]) -> None:
    from umbrella import lock

    lock.write(workdir, sources)


@pytest.fixture(autouse=True)
def nix_free(monkeypatch, request) -> None:
    """Answer the two questions that would need nix, from the lab.

    Autouse, so no test can reach the real `nix` by accident and pass or fail
    on what a machine happens to have. A test with no lab gets a specification
    that declares nothing, which is what a repo with no sources.nix has.
    """
    from umbrella import nixcli

    def spec(workdir: Path) -> dict[str, dict[str, str]]:
        found = (
            request.getfixturevalue("lab") if "lab" in request.fixturenames else None
        )
        return found.spec() if found else {}

    def prefetch(_workdir: Path, url: str, rev: str) -> dict:
        return node(url, rev)

    monkeypatch.setattr(nixcli, "spec", spec)
    monkeypatch.setattr(nixcli, "prefetch", prefetch)


@dataclass
class Checkout:
    """A working clone of the umbrella, already initialised."""

    path: Path

    def at(self, name: str) -> Path:
        """Where a source's working copy goes."""
        return self.path / name

    # The old name for it, kept because most of the suite reads better with a
    # short one.
    sub = at

    def cli(self, *args: str) -> int:
        """Run the command line in process, from inside this checkout."""
        from umbrella.cli import main

        previous = Path.cwd()
        os.chdir(self.path)
        try:
            return main(list(args))
        except SystemExit as exit_code:
            return int(exit_code.code or 0)
        finally:
            os.chdir(previous)

    def cli_stdin(self, payload: str, *args: str) -> int:
        """Run the command line with something on stdin, as a hook does."""
        import io

        from umbrella.cli import main

        previous = Path.cwd()
        was = sys.stdin
        os.chdir(self.path)
        sys.stdin = io.StringIO(payload)
        try:
            return main(list(args))
        except SystemExit as exit_code:
            return int(exit_code.code or 0)
        finally:
            sys.stdin = was
            os.chdir(previous)

    def umbrella(self):
        from umbrella.model import Umbrella

        previous = Path.cwd()
        os.chdir(self.path)
        try:
            return Umbrella.open()
        finally:
            os.chdir(previous)

    def backend(self):
        from umbrella import backend, mode

        return backend.for_mode(mode.read(self.umbrella().repo))

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return run("git", *args, cwd=cwd or self.path)

    def jj(self, source: str, *args: str) -> str:
        return run("jj", "--no-pager", "-R", str(self.at(source)), *args)

    @property
    def mode(self) -> str:
        from umbrella import mode as mode_module

        return str(mode_module.read(self.umbrella().repo))

    def commit(self, source: str, message: str) -> str:
        """Finish a commit the way this checkout's mode does."""
        if self.mode == "jj":
            return self.commit_jj(source, message)
        return self.commit_git(source, message)

    def peer(self, lab: "Lab", name: str) -> "Checkout":
        """Another checkout of the same umbrella, in the same mode."""
        other = Checkout(lab.clone(name))
        assert other.cli(*_init_args(self.mode)) == 0
        assert other.cli("fetch", "--all") == 0
        return other

    def publish(self, source: str) -> str:
        """Get this source's current commit onto its remote branch.

        The point of land, done by hand, so a guard can be tested without it.
        """
        head = self.head_of(source)
        if self.mode == "jj":
            self.jj(source, "bookmark", "move", "main", "--to", head)
            self.jj(source, "git", "push", "--bookmark", "main")
        else:
            attached = run(
                "git", "branch", "--show-current", cwd=self.at(source)
            ).strip()
            if attached != "main":
                # git refuses to force a branch that a worktree has checked out,
                # and when main is checked out it already points at head anyway.
                run("git", "branch", "-f", "main", head, cwd=self.at(source))
            run("git", "push", "-q", "origin", "main", cwd=self.at(source))
        return head

    def edit(self, source: str, text: str) -> None:
        (self.at(source) / "file.txt").write_text(f"{text}\n")

    def commit_git(self, source: str, message: str) -> str:
        run("git", "commit", "-qam", message, cwd=self.at(source))
        return self.head_of(source)

    def commit_jj(self, source: str, message: str) -> str:
        self.jj(source, "commit", "-m", message)
        return self.head_of(source)

    def head_of(self, source: str) -> str:
        return run("git", "rev-parse", "HEAD", cwd=self.at(source)).strip()

    def locked(self, source: str) -> str | None:
        """The revision the lock in this working copy names."""
        from umbrella import lock

        found = lock.read(self.path)
        return str(found[source]) if source in found else None

    def publish_umbrella(self, message: str) -> None:
        """Commit whatever land wrote, and push it.

        land stops at the lock now, because committing an umbrella is `git
        commit` under one VCS and `jj commit` under another, and picking one
        would be wrong in the other. This is the git half, done by the test.
        """
        run("git", "add", "--all", cwd=self.path)
        run("git", "commit", "-q", "--no-verify", "-m", message, cwd=self.path)
        run("git", "push", "-q", "origin", "main", cwd=self.path)

    def relock(self, source: str, rev: str) -> None:
        """Write one revision into the lock, without going through land."""
        from umbrella import lock

        entries = lock.entries(self.path)
        entries[source] = dict(entries[source], rev=rev, narHash=f"sha256-{rev[:6]}")
        lock.write(self.path, entries)


def _init_args(mode: str) -> list[str]:
    return ["init", "--jj"] if mode == "jj" else ["init"]


def _made(lab: Lab, name: str, mode: str) -> Checkout:
    checkout = Checkout(lab.clone(name))
    assert checkout.cli(*_init_args(mode)) == 0
    assert checkout.cli("fetch", "--all") == 0
    return checkout


@pytest.fixture
def git_checkout(lab: Lab) -> Checkout:
    return _made(lab, "git-work", "git")


@pytest.fixture
def jj_checkout(lab: Lab) -> Checkout:
    if not HAS_JJ:
        pytest.skip("jj is not installed")
    return _made(lab, "jj-work", "jj")


ZERO = "0" * 40


def push_line(local: str, remote: str = ZERO, ref: str = "refs/heads/main") -> str:
    """One line of what git feeds a pre-push hook on stdin."""
    return f"{ref} {local} {ref} {remote}\n"


@pytest.fixture(params=["git", "jj"])
def checkout(request, lab: Lab) -> Checkout:
    """An umbrella checkout in each mode.

    Anything that should behave the same under git and jj uses this, so the
    claim is asserted rather than assumed.
    """
    if request.param == "jj" and not HAS_JJ:
        pytest.skip("jj is not installed")
    return _made(lab, f"{request.param}-work", request.param)


@pytest.fixture(params=["git", "jj"])
def single(request, lab: Lab, tmp_path: Path) -> Checkout:
    """A standalone project in each mode, with no umbrella anywhere."""
    if request.param == "jj" and not HAS_JJ:
        pytest.skip("jj is not installed")
    path = tmp_path / f"single-{request.param}"
    run("git", "clone", "-q", str(lab.origin("sub1")), str(path))
    made = Checkout(path)
    assert made.cli(*_init_args(request.param)) == 0
    return made
