# Releases

Two umbrellas use this tool: `nixidae`, and
[`Lillecarl/pyterm`](https://github.com/Lillecarl/pyterm). They move
separately, so a change to the tool needs a number and a note saying what to
do about it.

There was no numbering before. Everything that shipped before 0.1.0 is
0.0.0.

## Contents

- [0.1.0 — a child can find the umbrella that locks it](#010--a-child-can-find-the-umbrella-that-locks-it)
  - [What changed](#what-changed)
  - [Porting from 0.0.0](#porting-from-000)
  - [What breaks if you do nothing](#what-breaks-if-you-do-nothing)
- [0.0.0 — the tool before numbering](#000--the-tool-before-numbering)

## 0.1.0 — a child can find the umbrella that locks it

**A child cannot hold the revision of the umbrella that locks it.** The
umbrella's commit holds the child's hash, so a child commit that held the
umbrella's hash would need a hash that contains itself. Measured over six
rounds in [`locking.md`](locking.md): twelve commits, and the lock was never
true of the current child.

0.1.0 answers that with a ref, because a ref is not in the tree.

### What changed

**`umbrella mark` is new.** It publishes one ref per locked revision on the
umbrella remote:

    refs/umbrella/<source>/<source revision>  ->  the umbrella commit

Run it after `umbrella land`, from the umbrella, with its HEAD already on
the remote. It refuses an umbrella commit no remote carries, because a ref
naming a commit only one machine holds helps nobody. It is idempotent, and
the **first** umbrella to lock a revision keeps the ref: a mutable ref would
give back the drift described below.

It maps only the sources that declare a `path` in `nix/sources.nix` — the
repositories worked on together. Nobody checks out nixpkgs to build it
against your umbrella, and a ref per nixpkgs bump is a ref per nixpkgs bump
forever. A specification that declares no path narrows nothing, and `mark`
says so rather than quietly publishing every source.

**`bin/walkback.sh` is new.** From a standalone child checkout it prints the
umbrella revision that locks the nearest landed ancestor of HEAD:

    bin/walkback.sh https://github.com/<owner>/<umbrella> <source name> [depth]

One `git ls-remote` and one `git rev-list`. A commit nobody landed has no
ref; its ancestors do, so the walk takes the first hit — the umbrella the
branch grew from, which is the one that supplies every other source. It
prints the revision on stdout and everything else on stderr, so a CI step
can redirect stdout into `$GITHUB_OUTPUT`.

It needs `declare -A`, so bash 4 or later. `ubuntu-24.04` runners have it;
macOS ships bash 3.2 by default.

**The child resolver has no unlocked arm left.** Each child's
`nix/sources.nix` resolves the umbrella in this order, and throws when
neither answers:

1. `UMBRELLA_REV` — what CI sets, once per run, from `walkback.sh`.
2. `nix/umbrella.rev` — a 40 character revision in the tree. It names the
   umbrella the work was written against, which is *not* the umbrella that
   locks the commit, and cannot be. The earlier revision is the useful one
   anyway: a build of this checkout overrides this repository with the
   checkout, so the umbrella supplies every *other* source.
3. A `throw` that names both.

**There is no fourth arm, and there cannot be one in Nix.**
`builtins.tryEval` does not catch a failed `fetchGit` — measured, not
assumed — so an arm that guessed at `refs/umbrella/<name>/<revision>` could
not fall back when that ref is absent. The mapping ref is read outside Nix,
by `walkback.sh`.

### Porting from 0.0.0

Three steps. They are independent, and step 1 is the one that matters.

**1. Write a pin file in every child.** `nix/umbrella.rev`, holding one 40
character umbrella revision and nothing else. Any umbrella commit that
locks a working version of that child will do; the newest is the obvious
choice.

    git -C <umbrella> rev-parse HEAD > <child>/nix/umbrella.rev

**2. Remove the unlocked arm from every child's `nix/sources.nix`.** If the
last arm of your umbrella reference is `github:<owner>/<umbrella>` or any
reference with no `rev=`, replace it with a `throw`. See any child of
`nixidae` for the shape.

This is the step that stops the drift. An unlocked reference resolves the
head of the default branch at evaluation time, every time `tarball-ttl` does
not answer from cache. So one commit of a child renders differently an hour
later, with nothing in it changed and no lock moved.

**3. Publish the refs, and read them in CI.**

In the umbrella, after `umbrella land`:

    umbrella mark

In each child, the job that resolves `UMBRELLA_REV` for the run needs a
checkout it can walk:

```yaml
umbrella-rev:
  runs-on: ubuntu-24.04
  timeout-minutes: 5
  outputs:
    rev: ${{ steps.resolve.outputs.rev }}
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 100
    - id: resolve
      run: |
        rev=$(ci/walkback.sh https://github.com/<owner>/<umbrella> <name>)
        echo "rev=$rev" >> "$GITHUB_OUTPUT"
```

`fetch-depth: 100` is the default to copy. The walk needs the branch point
of a branch nobody landed, and these repositories move fast. Depth 1 fails
the walk; measured in [`locking.md`](locking.md), a two step walk needs 5.

Vendor `walkback.sh` into each child as `ci/walkback.sh`. This job runs
before Nix does, and finding the umbrella is what it is for, so it cannot
come through the umbrella. A standalone contributor then has the tool in
their checkout, which is where the `throw` message points them.

### What breaks if you do nothing

Nothing in the tool. `mark` is a new command and every other command is
unchanged.

The child resolver is the breaking part, and it breaks only in the
repository where you make the change. A child with no `nix/umbrella.rev`,
built standalone, with no `UMBRELLA_REV` set, fails with a message naming
both. Inside the umbrella it never runs at all: each child's `default.nix`
takes `sources ? import ./nix/sources.nix`, the umbrella supplies that
argument, and a supplied default is never forced.

## 0.0.0 — the tool before numbering

Everything up to and including `umbrella land`: `init`, `fetch`, `mode`,
`status`, `sync`, `update`, `land`, `initcc`, `kind`, and the `check-commit`
and `check-push` guards. One lock file, `nix/sources.lock`, written by the
tool; one specification, `nix/sources.nix`, written by a human.
