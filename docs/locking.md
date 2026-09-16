# How a child finds the umbrella

Status: the constraint is decided. The mechanism is not.

**Carl's constraint, 2026-09-16: the umbrella must be pure. A standalone
child may be impure.** That rules out nothing below by itself, but it means
no approach has to be judged on whether it makes `github:Lillecarl/<child>`
a pure flake input.

This document holds what is measured, not what is believed. Every number
below comes from a run you can repeat. The scripts are in the appendix.

## Contents

- [The constraint](#the-constraint)
- [The question](#the-question)
- [The cycle, measured](#the-cycle-measured)
- [What a child can and cannot learn](#what-a-child-can-and-cannot-learn)
- [The seven approaches](#the-seven-approaches)
- [The matrix](#the-matrix)
- [Which children a change must rebuild](#which-children-a-change-must-rebuild)
- [Recommendation](#recommendation)
- [Appendix: repeating the runs](#appendix-repeating-the-runs)

## The constraint

The umbrella is pure and stays pure. A child on its own may be impure. So
the supported way to consume any of this is `nixidae`, and a standalone
checkout is for working on that project, not for depending on it.

This is already true, and measured: each child's `default.nix` takes
`sources ? import ./nix/sources.nix`, `nixidae/default.nix` supplies it, and
a supplied default argument is never forced. The child's own resolver — and
the branch-head fetch at the end of it — are unreachable through the
umbrella.

## The question

Every project here takes its dependencies from the umbrella. Inside the
umbrella that is easy: `nixidae/default.nix` passes `sources` to each child.

A child on its own has to find the umbrella by itself. That is the whole
problem, and it has one hard part: **the umbrella and the child each hold
the other's revision, and neither can hold a revision that does not exist
yet.**

## The cycle, measured

`umbrella land` writes the child's hash into the umbrella lock. That makes a
new umbrella commit. If the child then writes that umbrella's hash into a
file, the child gets a new hash, and the umbrella lock is out of date again.

Six rounds of letting both sides write, with real commits:

```
round  child      umbrella   lock names THIS child? pin names THIS umbrella?
1      3137309c   6b00aba2   NO -- stale            YES
2      b2e01316   0f4d3699   NO -- stale            YES
3      75e55842   77a67119   NO -- stale            YES
4      48c6eaa3   3ec58687   NO -- stale            YES
5      0c470bf5   be2dbc3b   NO -- stale            YES
6      80fa295d   592d56e7   NO -- stale            YES
```

Twelve commits, and the umbrella's lock is never true of the current child.

**You can have the umbrella's lock be true, or the child's pin be true. You
cannot have both.** That is not a bug in the tool. A commit hash covers the
whole tree, so a file in the tree that names a value derived from that same
hash has no solution.

`nix/umbrella.rev` today is the second column: the pin is true, and the lock
is one landing behind.

## What a child can and cannot learn

Measured with Nix 2.36 against local bare repositories.

| Question | Answer |
| --- | --- |
| Can a plain fetched tree learn its own revision? | **No.** It carries no `.git`. |
| Can a flake learn its own revision? | **Yes.** `self.rev` is present, and works under `--pure-eval`. |
| ...on a clean local checkout, as in CI? | **Yes.** |
| ...on a dirty working copy? | **No.** `rev` is gone and `dirtyRev` takes its place. |
| Can Nix fetch by an arbitrary ref? | **Yes.** `refs/umbrella/a/<sha>` resolved and returned the umbrella revision. |
| ...under `--pure-eval`? | **No.** *"in pure evaluation mode, 'fetchGit' doesn't fetch unlocked input"*. |
| Is there a primop that reads a ref or a note? | **No.** The only git builtins are `fetchGit`, `parseFlakeRef` and `flakeRefToString`. |
| Does a git note change the commit it annotates? | **No.** `b4f24078` before and after. |
| Does a plain `git clone` carry `refs/notes/*`? | **No.** The default refspec is `refs/heads/*` alone. |
| Does one extra refspec recover it? | **Yes**, on a full clone and on a shallow one: `git fetch origin 'refs/notes/*:refs/notes/*'`. |
| Does passing `sources` stop the child resolver evaluating? | **Yes.** A resolver that throws never fired when `sources` was supplied. |

Two of these decide most of what follows.

**Any lookup of a ref or a note happens outside evaluation.** No primop reads
one, and `fetchGit` with a ref and no revision is refused in pure mode. So a
ref scheme is something CI or `umbrella` resolves, and then passes in as
`UMBRELLA_REV`. The Nix side stays pure because it never does the lookup.

**The umbrella path is already pure.** Each child's `default.nix` takes
`sources ? import ./nix/sources.nix`, and `nixidae/default.nix` supplies it.
A supplied default argument is never forced. So the child resolver, and the
branch-head fetch at the end of it, are reachable **only** from a standalone
checkout: a child's own CI, a fork PR, or a direct
`github:Lillecarl/<child>` consumer.

That is the entire exposed surface. Everything below is about those three.

## The seven approaches

### A. A pin file in the child commit (today)

`nix/umbrella.rev` holds the umbrella the work was written against. Read
purely, survives a fork, survives a tarball.

No fixpoint, as above. The pin names the umbrella from *before* the landing,
so every sibling it supplies is one landing old. A child that needs a sibling
change landed in the same `umbrella land` does not get it, and nothing says
so.

### B. A lockfile in every child

A child names its **dependencies**, never the umbrella. The self-reference
disappears, so the fixpoint question does not arise.

Convergence needs the dependency graph to be acyclic. It is:

```
ghanix, pynixd, umbrella, flake-compatish   (leaves)
user-mode-nixos -> ghanix
easykubenix     -> nanopynix user-mode-nixos
nixkube         -> easykubenix ghanix user-mode-nixos
```

The apparent `nanopynix -> easykubenix` edge is a test fixture,
`pynix-lsp/tests/test_lsp/easykubenix/`. Every other mention in nanopynix's
`.nix` files is a comment.

Measured on a three-deep graph: one topological pass converged, and a second
pass produced **zero** commits. The cost is fan-out — one change to a leaf
rewrote two repositories above it. On the real graph, depth four, one
`ghanix` commit rewrites three.

**That fan-out is the churn already rejected once**, as "an update commit per
repository per day". Per-child locks are that same cost, spread out.

### C. The umbrella is the supported distribution

Accept that a standalone child is impure. Make `nixidae` the thing people
consume. A nixidae workflow triggers each child and passes the nixidae
revision as `UMBRELLA_REV`.

Nothing names the umbrella, so there is no cycle and no churn. The umbrella
path is already pure, measured above, so this changes nothing for a consumer
of nixidae.

What it gives up: `github:Lillecarl/nixkube` as a pure flake input.

A fork PR gets no `repository_dispatch` payload, so this needs a second arm
for that case. *(GitHub's behaviour here is not measured in the sandbox.)*

### D. A mapping ref in the umbrella

The umbrella publishes `refs/umbrella/<child>/<child-sha>`, pointing at the
umbrella commit that locks that child. No rewrite of the child, so no
fixpoint problem.

Measured: the ref pushes, `ls-remote` sees it, a default clone does **not**
fetch it, and `builtins.fetchGit` resolves it — impurely.

So the lookup is a CI step, not an evaluation. That is workable. It is also
one ref per child commit, forever, which is a ref namespace that only grows.

### E. A git note on the child commit

The umbrella locks child `C` at umbrella `U`, then writes a note on `C`
saying `U`. **The child commit does not change.** There is no second round,
so there is no fixpoint to fail to reach.

Measured end to end:

- the note left the commit hash identical, `c9b06db2` before and after;
- a plain clone did **not** carry it, and neither did a shallow one;
- one `git fetch origin 'refs/notes/*:refs/notes/*'` recovered it in both,
  so the cost is one refspec in CI and not a deeper checkout;
- a PR branch found its merge-base and read the note there, while the PR head
  stayed `c7e195a` — untouched.

That last line is the fork-PR arm that C needs. A contributor's branch has no
note of its own, because the umbrella has never locked it. Its merge-base
does. The umbrella that locked the branch point is a deterministic and
honest answer for a PR.

Like D, no primop reads it, so CI resolves it and exports `UMBRELLA_REV`.

### F. Children as flakes, using `self.rev`

A flake knows its own revision, purely. That is real self-knowledge, and no
other mechanism here has it.

It does not help on its own. Knowing `C` is only useful if something can map
`C` to an umbrella, and no primop does that lookup. So F still needs D or E
underneath, and it costs making every child a flake.

### G. Move the CI into nixidae

No child resolves anything, because no child builds on its own.

This also ends the property that a fork or a standalone contributor can build
a child at all. That is the thing worth keeping about this layout, and losing
it by accident would be a poor trade.

## The matrix

Measured in the sandbox unless marked. "Commits" is extra commits caused by
one change to a leaf project.

| | Settles? | Commits | Pure eval | Fork PR | Standalone flake input | Machinery |
| --- | --- | --- | --- | --- | --- | --- |
| **A** pin file *(today)* | **No** ¹ | 2 per round, forever ¹ | Yes | Yes | Stale but deterministic | None, it exists |
| **B** lockfile per child | Yes ² | 3 on the real graph ² | Yes | Yes | Yes | N locks, topological writer |
| **C** umbrella-only + trigger | N/A, nothing names it | 0 | Yes ³ | Needs another arm ⁴ | **No** | Workflow + token |
| **D** mapping ref | Yes | 0 | **No** ⁵, CI resolves it | Yes, via CI | No | Growing ref namespace |
| **E** git note | Yes | 0 | No primop ⁶, CI resolves it | **Yes, via merge-base** ⁷ | No | One notes refspec |
| **F** flakes + `self.rev` | Needs D or E | 0 | `self.rev` yes ⁸ | Yes | Yes | Every child becomes a flake |
| **G** CI into nixidae | N/A | 0 | Yes | Only against nixidae | No | Large CI move |

¹ six-round run above ·
² one pass converged, second pass zero commits ·
³ the `sources` laziness proof ·
⁴ GitHub dispatch and forks, **not measured here** ·
⁵ `--pure-eval` refused `fetchGit` with a ref ·
⁶ only `fetchGit`, `parseFlakeRef`, `flakeRefToString` exist ·
⁷ merge-base note read while the PR head stayed unchanged ·
⁸ present under `--pure-eval` and on a clean checkout, absent when dirty

## Which children a change must rebuild

Triggering every child on every landing wastes most of a CI run. A change to
`nanopynix` has no bearing on `pynixd`, because `pynixd` does not use it.

**The umbrella already holds the data to work this out.** `nix/sources.nix`
names every project, and which projects a given one uses is a property of its
tree. The graph below comes from reading each project for the sources it
names, cross-checked by hand for the one case a text search cannot settle:
`easykubenix/default.nix:26` passes `sources` down into `nanopynix`, and
`nanopynix` reads no sibling out of it.

```
ghanix, pynixd, umbrella, flake-compatish   (use no sibling)
nanopynix                                    (uses no sibling)
user-mode-nixos -> ghanix
easykubenix     -> nanopynix user-mode-nixos
nixkube         -> easykubenix ghanix user-mode-nixos
```

Take the reverse closure and a landing triggers this much:

| changed | CI must run | skipped |
| --- | --- | --- |
| `easykubenix` | easykubenix, nixkube | 6 of 8 |
| `flake-compatish` | flake-compatish | 7 of 8 |
| `ghanix` | easykubenix, ghanix, nixkube, user-mode-nixos | 4 of 8 |
| `nanopynix` | easykubenix, nanopynix, nixkube | 5 of 8 |
| `nixkube` | nixkube | 7 of 8 |
| `pynixd` | pynixd | 7 of 8 |
| `umbrella` | umbrella | 7 of 8 |
| `user-mode-nixos` | easykubenix, nixkube, user-mode-nixos | 5 of 8 |

**A `nanopynix` change does not run `pynixd`.** The worst case is `ghanix`,
at four of eight, and half the projects trigger themselves alone.

This is not wishful thinking: the graph is derived, not hand-kept, so it
cannot drift from the code the way a hand-written trigger list would. The
honest caveat is the derivation. A text search finds a name a project
mentions; it does not prove the project *forces* it. An exact answer wraps
each source in `builtins.trace` and records which ones a full evaluation
touches. That is worth doing before the list drives real triggering, and it
is one evaluation per project.

## Recommendation

**C with E**, and treat the trigger as an optimisation rather than the
mechanism.

The constraint settles C: a standalone child may be impure, so the only
thing C gives up costs nothing. And C is most of the way done already — the
umbrella path is pure today, measured, and the only exposed surface is the
standalone one.

E fills that surface with the one mechanism that has no fixpoint and no
rewrite. The umbrella's write side becomes "push a note" instead of "amend
the child", which removes the churn that A pays and that B would pay in a
different currency. `umbrella land` gains one push; no child gains a
commit.

E also answers the PR case without a round-trip, which was the open part of
C. A contributor's branch reads the note on its merge-base. That is one git
command and it leaves the contributor's commit alone.

The trigger then buys latency, not correctness: a note on the landed commit
*is* the revision the trigger would carry, so a dispatch only makes it arrive
sooner. Worth adding later, not first.

Keep `nix/umbrella.rev` while this is undecided. It is in six projects, it is
read purely, and stale-but-deterministic beats the branch head. Under C it
stops being load-bearing.

**What would flip this.** If a fork PR's CI cannot fetch notes from upstream,
E loses its best property and C needs the round-trip after all. That is not
measured here, and it is checkable with one workflow run.

**What would flip it to B.** Somebody needing `github:Lillecarl/nixkube` as a
pure flake input. B is the only row that gives it. Nothing needs it today.

## Appendix: repeating the runs

The sandbox builds four bare repositories and three children, with no
network:

```
scratchpad/lockdance/
  remotes/{umbrella,a,b,c}.git   bare, used as real remotes
  work/{umbrella,a,b,c}          working copies
  fixpoint.sh                    the six-round run
  dag.sh                         the topological pass
```

`bash fixpoint.sh <dir>` prints the six-round table.
`bash dag.sh <dir>` prints the convergence run.

The one-off probes — `self.rev`, `fetchGit` by ref under `--pure-eval`, note
survival across clone shapes, the `sources` laziness proof — are single
commands and are quoted where they are used above.
