"""The refs that map a source revision to the umbrella that locks it.

A child cannot hold the revision of the umbrella that locks it, because the
umbrella's commit holds the child's hash. A ref is not in the tree, so it
escapes that, and `mark` is what publishes them.
"""

from __future__ import annotations

from conftest import Checkout, run

from umbrella import mapping


def _umbrella_head(checkout: Checkout) -> str:
    return checkout.git("rev-parse", "HEAD").strip()


def _refs(checkout: Checkout) -> dict[str, str]:
    """Every mapping ref in the checkout, as ref name -> target."""
    listing = checkout.git(
        "for-each-ref", "--format=%(refname) %(objectname)", mapping.PREFIX
    )
    return dict(line.split(" ", 1) for line in listing.splitlines() if line.strip())


def _publish(checkout: Checkout) -> None:
    """Put the umbrella's HEAD on its remote, which `mark` insists on."""
    checkout.git("push", "origin", "HEAD:refs/heads/main")


def test_mark_maps_every_locked_revision_to_this_umbrella(
    checkout: Checkout,
) -> None:
    _publish(checkout)
    head = _umbrella_head(checkout)

    assert checkout.cli("mark", "--no-push") == 0

    written = _refs(checkout)
    assert written, "mark wrote no refs"
    for ref, target in written.items():
        assert ref.startswith(f"{mapping.PREFIX}/")
        # Every ref points at the umbrella that published it, and its last
        # segment is the revision it maps.
        assert target == head
        assert len(ref.rsplit("/", 1)[1]) == 40


def test_mark_refuses_an_umbrella_that_is_on_no_remote(checkout: Checkout) -> None:
    """A ref naming a commit only this machine holds helps nobody.

    The fixture's umbrella is already pushed, so this makes a commit on top
    and leaves it where it is.
    """
    (checkout.path / "unpushed.txt").write_text("not on any remote\n")
    checkout.git("add", "--", "unpushed.txt")
    checkout.git("commit", "-m", "a commit nobody else has")

    assert checkout.cli("mark", "--no-push") == 1
    assert _refs(checkout) == {}


def test_mark_is_idempotent(checkout: Checkout) -> None:
    _publish(checkout)
    assert checkout.cli("mark", "--no-push") == 0
    first = _refs(checkout)

    assert checkout.cli("mark", "--no-push") == 0

    assert _refs(checkout) == first


def test_the_first_umbrella_to_lock_a_revision_keeps_the_ref(
    checkout: Checkout,
) -> None:
    """Otherwise the ref is mutable again, which is the whole thing it avoids.

    A source nobody touched stays locked at one revision across many umbrella
    commits, so each of them could claim its ref.
    """
    _publish(checkout)
    assert checkout.cli("mark", "--no-push") == 0
    first = _refs(checkout)
    first_head = _umbrella_head(checkout)

    # A second umbrella commit that locks the same revisions.
    (checkout.path / "unrelated.txt").write_text("a change that locks nothing new\n")
    checkout.git("add", "--", "unrelated.txt")
    checkout.git("commit", "-m", "an umbrella commit that moves no lock")
    _publish(checkout)
    assert _umbrella_head(checkout) != first_head

    assert checkout.cli("mark", "--no-push") == 0

    assert _refs(checkout) == first, "the second umbrella took refs from the first"


def test_mark_publishes_the_refs_to_the_remote(checkout: Checkout) -> None:
    _publish(checkout)

    assert checkout.cli("mark") == 0

    remote = run("git", "ls-remote", "origin", f"{mapping.PREFIX}/*", cwd=checkout.path)
    assert remote.strip(), "mark pushed nothing"
    names = {line.split("\t", 1)[1] for line in remote.splitlines() if line.strip()}
    assert names == set(_refs(checkout))


def test_the_ref_name_carries_the_source_and_the_revision() -> None:
    assert mapping.name("nixkube", "0" * 40) == f"refs/umbrella/nixkube/{'0' * 40}"


def test_ours_keeps_only_the_sources_with_a_working_copy() -> None:
    """`path` in the specification is the line between ours and theirs."""
    spec = {
        "nixkube": {"url": "...", "branch": "develop", "path": "/w/nixkube"},
        "nixpkgs": {"url": "...", "branch": "nixpkgs-unstable"},
    }
    locked = {"nixkube": "a" * 40, "nixpkgs": "b" * 40}

    kept, narrowed = mapping.ours(spec, locked)

    assert kept == {"nixkube": "a" * 40}
    assert narrowed is True


def test_ours_keeps_everything_when_the_specification_says_nothing() -> None:
    """A checkout with no nix cannot tell ours from theirs, and says so."""
    locked = {"nixkube": "a" * 40, "nixpkgs": "b" * 40}

    kept, narrowed = mapping.ours({}, locked)

    assert kept == locked
    assert narrowed is False


def test_mark_publishes_refs_an_earlier_run_left_local(checkout: Checkout) -> None:
    """A `--no-push` run leaves refs behind; the next push must pick them up.

    Otherwise they stay local forever: every later run finds them written and
    skips them again.
    """
    _publish(checkout)
    assert checkout.cli("mark", "--no-push") == 0
    local = set(_refs(checkout))
    assert local, "the first run wrote nothing"
    assert not run(
        "git", "ls-remote", "origin", f"{mapping.PREFIX}/*", cwd=checkout.path
    ).strip(), "--no-push published something"

    assert checkout.cli("mark") == 0

    remote = run(
        "git", "ls-remote", "origin", f"{mapping.PREFIX}/*", cwd=checkout.path
    )
    names = {line.split("\t", 1)[1] for line in remote.splitlines() if line.strip()}
    assert names == local
