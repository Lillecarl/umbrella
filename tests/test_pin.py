"""The umbrella revision `land` records inside each source it publishes.

The pin goes into the commit being landed, not beside it. That is the whole
compromise: a project with work to publish carries the umbrella it was written
against, and a project nobody touched gains no commit at all.
"""

from __future__ import annotations

import pytest

from conftest import ZERO, Checkout, needs_jj, run

from umbrella import pin


def _adopt(checkout: Checkout, source: str, revision: str = ZERO) -> str:
    """Give a source a pin file, the way a project adopts one by hand."""
    directory = checkout.at(source) / "nix"
    directory.mkdir(exist_ok=True)
    (directory / "umbrella.rev").write_text(f"{revision}\n")
    if checkout.mode == "git":
        run("git", "add", "--", pin.PATH, cwd=checkout.at(source))
    return checkout.commit(source, "adopt the umbrella pin")


def _pinned(checkout: Checkout, source: str) -> str | None:
    return pin.read(checkout.at(source))


def _umbrella_head(checkout: Checkout) -> str:
    return checkout.git("rev-parse", "HEAD").strip()


def _commits(checkout: Checkout, source: str) -> int:
    return int(run("git", "rev-list", "--count", "HEAD", cwd=checkout.at(source)))


def test_land_writes_the_pin_into_the_commit_it_publishes(checkout: Checkout) -> None:
    adopted = _adopt(checkout, "sub1")
    before = _commits(checkout, "sub1")

    assert checkout.cli("land") == 0

    head = checkout.head_of("sub1")
    assert head != adopted, "the pin belongs in the commit, so the commit changes"
    assert _pinned(checkout, "sub1") == _umbrella_head(checkout)
    assert _commits(checkout, "sub1") == before, "amended, not one commit more"
    assert checkout.locked("sub1") == head
    on_origin = run("git", "rev-parse", "origin/main", cwd=checkout.at("sub1")).strip()
    assert on_origin == head


def test_land_keeps_the_message_of_the_commit_it_pins(checkout: Checkout) -> None:
    _adopt(checkout, "sub1")
    checkout.edit("sub1", "v2")
    checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    said = run("git", "log", "-1", "--format=%s", cwd=checkout.at("sub1")).strip()
    assert said == "v2"


def test_land_creates_no_pin_where_a_source_carries_none(checkout: Checkout) -> None:
    """Adopting the pin is the project's decision, not this tool's."""
    checkout.edit("sub1", "v2")
    landed = checkout.commit("sub1", "v2")

    assert checkout.cli("land") == 0

    assert checkout.head_of("sub1") == landed
    assert not pin.carried_by(checkout.at("sub1"))


def test_land_rewrites_nothing_when_the_pin_already_matches(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    landed = _adopt(checkout, "sub1", _umbrella_head(checkout))

    assert checkout.cli("land") == 0

    assert checkout.head_of("sub1") == landed
    assert checkout.locked("sub1") == landed
    # It says so. A skip that prints nothing cannot be told apart from the
    # pin never running at all.
    assert "already names" in capsys.readouterr().out


def test_land_leaves_a_published_commit_alone(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """Amending a commit somebody can already fetch rewrites their history."""
    landed = _adopt(checkout, "sub1")
    checkout.publish("sub1")

    assert checkout.cli("land") == 0

    assert checkout.head_of("sub1") == landed
    assert _pinned(checkout, "sub1") == ZERO
    assert "already on a remote" in capsys.readouterr().out


def test_land_writes_no_pin_nobody_can_fetch(
    checkout: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin naming a private umbrella makes the source unbuildable.

    The pin already in the file is public and still correct, only older, so
    leaving it is the safe answer.
    """
    landed = _adopt(checkout, "sub1")
    checkout.git("commit", "--allow-empty", "--no-verify", "-q", "-m", "not pushed")

    assert checkout.cli("land") == 0

    assert checkout.head_of("sub1") == landed
    assert _pinned(checkout, "sub1") == ZERO
    assert "is on no remote" in capsys.readouterr().err


def test_land_writes_no_pin_with_no_advance(checkout: Checkout) -> None:
    """--no-advance moves nothing, and the pin moves the commit."""
    landed = _adopt(checkout, "sub1")
    checkout.publish("sub1")

    assert checkout.cli("land", "--no-advance") == 0

    assert checkout.head_of("sub1") == landed
    assert _pinned(checkout, "sub1") == ZERO


@needs_jj
def test_land_pins_a_colocated_source_in_git_mode(git_checkout: Checkout) -> None:
    """The mode is the checkout's choice. Colocation is the source's fact.

    `git commit --amend` here would move HEAD behind jj's back, and jj would
    import the amended commit as a second head with @ still on the old one.
    """
    source = git_checkout.at("sub1")
    run("jj", "git", "init", "--colocate", str(source))
    (source / "nix").mkdir(exist_ok=True)
    (source / "nix" / "umbrella.rev").write_text(f"{ZERO}\n")
    # jj, because that is what somebody with a colocated source uses. Going
    # through git here would leave @ on the commit before it and test a shape
    # nobody has.
    run("jj", "--no-pager", "-R", str(source), "commit", "-m", "adopt the pin")
    before = _commits(git_checkout, "sub1")

    assert git_checkout.cli("land") == 0

    assert _pinned(git_checkout, "sub1") == _umbrella_head(git_checkout)
    assert _commits(git_checkout, "sub1") == before
    said = run("git", "log", "-1", "--format=%s", cwd=source).strip()
    assert said == "adopt the pin", "the pin went into the commit being landed"
    empty = run(
        "jj",
        "--no-pager",
        "-R",
        str(source),
        "log",
        "--no-graph",
        "-r",
        "@",
        "-T",
        "empty",
    ).strip()
    assert empty == "true", "the pin left the working copy"
    heads = run(
        "jj",
        "--no-pager",
        "-R",
        str(source),
        "log",
        "--no-graph",
        "-r",
        "heads(all())",
        "-T",
        'commit_id ++ "\n"',
    )
    assert len([line for line in heads.splitlines() if line.strip()]) == 1


@needs_jj
def test_land_pins_under_a_described_working_commit(jj_checkout: Checkout) -> None:
    """`jj describe` with no `jj new` after it leaves @ empty and described.

    Squashing the pin in would then abandon @ and ask for a combined
    description in an editor, which hangs a run nobody is watching.
    `--keep-emptied` is what stops it, and this is the test that proves it.
    """
    _adopt(jj_checkout, "sub1")
    jj_checkout.jj("sub1", "describe", "-m", "work in progress")

    assert jj_checkout.cli("land") == 0

    assert _pinned(jj_checkout, "sub1") == _umbrella_head(jj_checkout)
    said = jj_checkout.jj(
        "sub1", "log", "--no-graph", "-r", "@", "-T", "description"
    ).strip()
    assert said == "work in progress"
