"""A lab of real repositories, built fresh for every test.

Nothing here is mocked. Each fixture builds bare origins and working clones on
disk, so a test exercises the same git and jj that a user would.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

HAS_JJ = shutil.which("jj") is not None
needs_jj = pytest.mark.skipif(not HAS_JJ, reason="jj is not installed")


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


@dataclass
class Lab:
    """Bare origins plus the seed content that was pushed to them."""

    root: Path
    origins: Path

    def origin(self, name: str) -> Path:
        return self.origins / f"{name}.git"

    def clone(self, name: str = "work") -> Path:
        """A plain clone of the umbrella. No --recurse-submodules on purpose."""
        dest = self.root / name
        run("git", "clone", "-q", str(self.origin("umbrella")), str(dest))
        return dest

    def push_from_elsewhere(self, sub: str, text: str) -> str:
        """Advance a submodule's main on the origin, behind everyone's back.

        This is how a test creates a stale remote-tracking ref.
        """
        work = self.root / f"_side-{sub}-{text}"
        run("git", "clone", "-q", str(self.origin(sub)), str(work))
        (work / "file.txt").write_text(text)
        run("git", "commit", "-qam", text, cwd=work)
        run("git", "push", "-q", "origin", "main", cwd=work)
        return run("git", "rev-parse", "HEAD", cwd=work).strip()


def _seed_sub(lab: Lab, name: str) -> None:
    origin = lab.origin(name)
    run("git", "init", "--bare", "-q", str(origin))
    work = lab.root / f"_seed-{name}"
    run("git", "init", "-q", "-b", "main", str(work))
    (work / "file.txt").write_text(f"{name} v1\n")
    run("git", "add", ".", cwd=work)
    run("git", "commit", "-qm", f"seed {name}", cwd=work)
    run("git", "remote", "add", "origin", str(origin), cwd=work)
    run("git", "push", "-q", "origin", "main", cwd=work)


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    """Two submodule projects and an umbrella that tracks them."""
    made = Lab(root=tmp_path, origins=tmp_path / "origins")
    made.origins.mkdir()

    for name in ("sub1", "sub2"):
        _seed_sub(made, name)

    run("git", "init", "--bare", "-q", str(made.origin("umbrella")))
    seed = tmp_path / "_seed-umbrella"
    run("git", "init", "-q", "-b", "main", str(seed))
    for name in ("sub1", "sub2"):
        run("git", "submodule", "add", "-q", str(made.origin(name)), name, cwd=seed)
    run("git", "commit", "-qm", "seed umbrella", cwd=seed)
    run("git", "remote", "add", "origin", str(made.origin("umbrella")), cwd=seed)
    run("git", "push", "-q", "origin", "main", cwd=seed)
    return made


@dataclass
class Checkout:
    """A working clone of the umbrella, already initialised."""

    path: Path

    def sub(self, name: str) -> Path:
        return self.path / name

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

    def jj(self, sub: str, *args: str) -> str:
        return run("jj", "--no-pager", "-R", str(self.sub(sub)), *args)

    @property
    def mode(self) -> str:
        from umbrella import mode as mode_module

        return str(mode_module.read(self.umbrella().repo))

    def commit(self, sub: str, message: str) -> str:
        """Finish a commit the way this checkout's mode does."""
        if self.mode == "jj":
            return self.commit_jj(sub, message)
        return self.commit_git(sub, message)

    def peer(self, lab: "Lab", name: str) -> "Checkout":
        """Another checkout of the same umbrella, in the same mode."""
        other = Checkout(lab.clone(name))
        assert other.cli(f"init{self.mode}") == 0
        return other

    def publish(self, sub: str) -> str:
        """Get this submodule's current commit onto its remote branch.

        The point of land, done by hand, so a guard can be tested without it.
        """
        head = self.head_of(sub)
        if self.mode == "jj":
            self.jj(sub, "bookmark", "move", "main", "--to", head)
            self.jj(sub, "git", "push", "--bookmark", "main")
        else:
            attached = run("git", "branch", "--show-current", cwd=self.sub(sub)).strip()
            if attached != "main":
                # git refuses to force a branch that a worktree has checked out,
                # and when main is checked out it already points at head anyway.
                run("git", "branch", "-f", "main", head, cwd=self.sub(sub))
            run("git", "push", "-q", "origin", "main", cwd=self.sub(sub))
        return head

    def edit(self, sub: str, text: str) -> None:
        (self.sub(sub) / "file.txt").write_text(f"{text}\n")

    def commit_git(self, sub: str, message: str) -> str:
        run("git", "commit", "-qam", message, cwd=self.sub(sub))
        return self.head_of(sub)

    def commit_jj(self, sub: str, message: str) -> str:
        self.jj(sub, "commit", "-m", message)
        return self.head_of(sub)

    def head_of(self, sub: str) -> str:
        return run("git", "rev-parse", "HEAD", cwd=self.sub(sub)).strip()

    def recorded(self, sub: str) -> str:
        return run("git", "rev-parse", f"HEAD:{sub}", cwd=self.path).strip()


@pytest.fixture
def git_checkout(lab: Lab) -> Checkout:
    checkout = Checkout(lab.clone("git-work"))
    assert checkout.cli("initgit") == 0
    return checkout


@pytest.fixture
def jj_checkout(lab: Lab) -> Checkout:
    checkout = Checkout(lab.clone("jj-work"))
    assert checkout.cli("initjj") == 0
    return checkout


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
    made = Checkout(lab.clone(f"{request.param}-work"))
    assert made.cli(f"init{request.param}") == 0
    return made


@pytest.fixture(params=["git", "jj"])
def single(request, lab: Lab, tmp_path: Path) -> Checkout:
    """A standalone project in each mode, with no umbrella anywhere."""
    if request.param == "jj" and not HAS_JJ:
        pytest.skip("jj is not installed")
    path = tmp_path / f"single-{request.param}"
    run("git", "clone", "-q", str(lab.origin("sub1")), str(path))
    made = Checkout(path)
    assert made.cli(f"init{request.param}") == 0
    return made
