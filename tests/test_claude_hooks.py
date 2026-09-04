"""Claude's WorktreeCreate and WorktreeRemove hooks.

The contract is narrow. Both read one json object on stdin. Create prints the
directory it made on stdout and nothing else, because Claude reads that path.
Everything a person would want to read goes to stderr.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import Checkout, needs_jj, run

CREATE = ("hook", "worktree-create")
REMOVE = ("hook", "worktree-remove")


def _payload(name: str) -> str:
    return json.dumps({"name": name})


def _hook_dir(project: Path, name: str) -> Path:
    return project / ".claude" / "worktrees" / name


def test_create_prints_only_the_path(
    single: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))

    assert single.cli_stdin(_payload("task"), *CREATE) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [str(_hook_dir(single.path, "task"))]


def test_create_puts_it_where_the_plugin_expects(
    single: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))

    assert single.cli_stdin(_payload("task"), *CREATE) == 0
    capsys.readouterr()

    made = _hook_dir(single.path, "task")
    assert made.is_dir()
    assert (made / "file.txt").read_text() == "sub1 v1\n"


def test_create_hides_the_worktrees_directory_from_git(
    single: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It lives inside the project, so without this it is untracked clutter."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))

    assert single.cli_stdin(_payload("task"), *CREATE) == 0
    capsys.readouterr()

    assert run("git", "status", "--porcelain", cwd=single.path) == ""


def test_remove_deletes_it(
    single: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))
    assert single.cli_stdin(_payload("task"), *CREATE) == 0
    capsys.readouterr()
    made = _hook_dir(single.path, "task")
    assert made.exists()

    assert single.cli_stdin(_payload("task"), *REMOVE) == 0

    assert not made.exists()


def test_create_and_remove_can_be_repeated(
    single: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An agent will churn through these, so the name has to be reusable."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))

    for _ in range(3):
        assert single.cli_stdin(_payload("task"), *CREATE) == 0
        assert single.cli_stdin(_payload("task"), *REMOVE) == 0
        capsys.readouterr()

    assert not _hook_dir(single.path, "task").exists()


def test_create_refuses_a_payload_with_no_name(
    single: Checkout, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))

    assert single.cli_stdin("{}", *CREATE) == 1


def test_create_refuses_a_payload_that_is_not_json(
    single: Checkout, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))

    assert single.cli_stdin("not json at all", *CREATE) == 1


def test_create_follows_claude_project_dir_not_the_working_directory(
    single: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude starts a hook wherever it likes, so the env var decides."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(single.path))
    elsewhere = Checkout(single.path.parent)

    assert elsewhere.cli_stdin(_payload("task"), *CREATE) == 0

    assert capsys.readouterr().out.strip() == str(_hook_dir(single.path, "task"))


def test_create_on_an_umbrella_makes_the_whole_constellation(
    checkout: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(checkout.path))

    assert checkout.cli_stdin(_payload("task"), *CREATE) == 0
    capsys.readouterr()

    made = _hook_dir(checkout.path, "task")
    for name in ("sub1", "sub2"):
        assert (made / name / "file.txt").read_text() == f"{name} v1\n"


def test_remove_on_an_umbrella_takes_the_constellation_with_it(
    checkout: Checkout, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(checkout.path))
    assert checkout.cli_stdin(_payload("task"), *CREATE) == 0
    capsys.readouterr()

    assert checkout.cli_stdin(_payload("task"), *REMOVE) == 0

    assert not _hook_dir(checkout.path, "task").exists()


# -- where a new workspace starts ------------------------------------------


@needs_jj
def test_the_base_revset_falls_back_when_trunk_is_root(tmp_path: Path) -> None:
    """trunk() resolves to root() with no remote, and forking there is empty."""
    from umbrella import jj

    local = tmp_path / "local-only"
    run("jj", "--no-pager", "git", "init", str(local))
    (local / "f.txt").write_text("one\n")
    run("jj", "--no-pager", "-R", str(local), "commit", "-m", "one")

    assert jj.base_revision(local, None) is None


@needs_jj
def test_the_base_revset_is_used_when_trunk_exists(single: Checkout) -> None:
    from umbrella import jj

    if single.mode != "jj":
        pytest.skip("git mode has no revsets")

    assert jj.base_revision(single.path, None) == "trunk()"


@needs_jj
def test_the_base_revset_can_be_overridden(single: Checkout, monkeypatch) -> None:
    from umbrella import jj

    if single.mode != "jj":
        pytest.skip("git mode has no revsets")
    monkeypatch.setenv("JJ_WORKTREES_BASE_REVSET", "@")

    assert jj.base_revision(single.path, None) == "@"


def test_create_excludes_its_directory_from_a_linked_worktree(
    lab, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """info/exclude lives in the common git directory and is shared.

    A linked worktree has its own git directory, but git never reads an
    info/exclude from it, so writing there would silently do nothing.
    """
    project = tmp_path / "proj"
    run("git", "clone", "-q", str(lab.origin("sub1")), str(project))
    assert Checkout(project).cli("initgit") == 0

    linked = tmp_path / "proj-linked"
    run("git", "worktree", "add", "-q", "-b", "linked", str(linked), cwd=project)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(linked))

    assert Checkout(linked).cli_stdin(_payload("task"), *CREATE) == 0
    capsys.readouterr()

    assert run("git", "status", "--porcelain", cwd=linked) == ""


# -- stdout has to be the path and nothing else ----------------------------


def _as_subprocess(project: Path, *args: str, payload: str) -> "subprocess.CompletedProcess[str]":
    import os
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "umbrella", *args],
        cwd=project,
        input=payload,
        capture_output=True,
        text=True,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project)),
    )


def test_create_prints_only_the_path_when_run_as_a_process(
    single: Checkout,
) -> None:
    """Run it for real.

    In process, capsys sees what this program prints but not what a child
    process writes to the real stdout. git worktree add says "HEAD is now at
    ..." there, and that is how it got past the in-process tests.
    """
    done = _as_subprocess(
        single.path, "hook", "worktree-create", payload=_payload("task")
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [str(_hook_dir(single.path, "task"))]


def test_create_on_an_umbrella_prints_only_the_path_as_a_process(
    checkout: Checkout,
) -> None:
    done = _as_subprocess(
        checkout.path, "hook", "worktree-create", payload=_payload("task")
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [str(_hook_dir(checkout.path, "task"))]
