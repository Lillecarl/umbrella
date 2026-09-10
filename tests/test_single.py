"""A project with no umbrella at all, which is what most repos are."""

from __future__ import annotations

import pytest

from conftest import Checkout, run

from umbrella.kind import Kind


def test_a_single_project_knows_what_it_is(single: Checkout) -> None:
    assert single.umbrella().kind is Kind.SINGLE
    assert single.umbrella().sources() == []


def test_init_jj_colocates_the_project_itself(single: Checkout) -> None:
    """An umbrella colocates its working copies. A single project has only itself."""
    if single.mode != "jj":
        pytest.skip("git mode colocates nothing")
    assert (single.path / ".jj").is_dir()


def test_status_says_there_is_nothing_to_track(
    single: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert single.cli("status") == 0

    out = capsys.readouterr().out
    assert "single" in out
    assert "no lock to track" in out


def test_land_refuses_on_a_single_project(single: Checkout) -> None:
    assert single.cli("land") == 1


def test_sync_refuses_on_a_single_project(single: Checkout) -> None:
    assert single.cli("sync") == 1


def test_a_wts_of_a_single_project_is_one_working_copy(single: Checkout) -> None:
    assert single.cli("wts", "add", "poc") == 0

    made = single.path.parent / f"{single.path.name}-poc"
    assert (made / "file.txt").read_text() == "sub1 v1\n"


def test_a_wts_of_a_single_project_shares_its_storage(single: Checkout) -> None:
    """A worktreespace is never a second clone, in either mode."""
    assert single.cli("wts", "add", "poc") == 0
    made = single.path.parent / f"{single.path.name}-poc"

    if single.mode == "jj":
        assert "poc:" in run(
            "jj", "--no-pager", "-R", str(single.path), "workspace", "list"
        )
        assert not (made / ".git").exists()  # a workspace, not a checkout of git
    else:
        # A worktree points into the repo it came from rather than owning one.
        assert (made / ".git").is_file()
        assert str(single.path) in (made / ".git").read_text()


def test_work_in_a_wts_does_not_disturb_the_project(single: Checkout) -> None:
    assert single.cli("wts", "add", "poc") == 0
    made = single.path.parent / f"{single.path.name}-poc"

    (made / "file.txt").write_text("changed in the wts\n")

    assert (single.path / "file.txt").read_text() == "sub1 v1\n"


def test_removing_a_wts_of_a_single_project(single: Checkout) -> None:
    assert single.cli("wts", "add", "poc") == 0
    made = single.path.parent / f"{single.path.name}-poc"
    assert made.exists()

    assert single.cli("wts", "rm", "poc") == 0

    assert not made.exists()


def test_two_worktreespaces_coexist(single: Checkout) -> None:
    assert single.cli("wts", "add", "one") == 0
    assert single.cli("wts", "add", "two") == 0

    for name in ("one", "two"):
        assert (single.path.parent / f"{single.path.name}-{name}" / "file.txt").exists()


def test_a_wts_name_cannot_be_reused_while_it_exists(single: Checkout) -> None:
    assert single.cli("wts", "add", "poc") == 0

    assert single.cli("wts", "add", "poc") == 1


def test_a_single_project_gets_no_hooks(single: Checkout) -> None:
    """The guards only look at locked revisions, and there are none.

    Taking over core.hooksPath to install two hooks that can do nothing would
    disable whatever hooks the repo already has.
    """
    assert not (single.path / ".githooks").exists()
    with pytest.raises(KeyError):
        single.umbrella().repo.config["core.hooksPath"]


def test_a_single_project_is_not_given_an_ignore_block(single: Checkout) -> None:
    """Nothing is fetched into it, so there is nothing to keep out."""
    from umbrella import ignore

    assert not (single.path / ignore.FILE).exists()
