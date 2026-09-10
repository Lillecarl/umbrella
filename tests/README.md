# Test lab

Every fixture builds real repositories on disk: bare origins under `origins/`,
then working clones. Nothing is mocked, so a test runs the same git and jj a
user would.

nix is the one exception. `update` and `land` read `nix/sources.nix` and
prefetch a revision, and neither works in a build sandbox: there is no network,
and recursive nix is off. The autouse `nix_free` fixture answers those two
questions from the lab. Every other decision, including which revision the lock
ends up naming, is made against the real repositories.

`lab` gives two source projects and an umbrella whose `nix/sources.lock` names
them. A clone of that umbrella has no working copies, which is the normal shape:
a source resolves from the lock until somebody runs `umbrella fetch`.
`git_checkout` and `jj_checkout` each clone the umbrella, initialise it in one
mode and fetch both sources. `Checkout.cli()` runs the command line in process
from inside the checkout, and the hook tests call `git` itself, so the hooks run
the way git runs them.

One thing to know when running the suite by hand: the git hooks prefer an
`umbrella` on PATH over the shim, so an older build in the dev shell will run
instead of the working copy. `nix build --file . umbrella` has a clean PATH and
is the check that counts.
