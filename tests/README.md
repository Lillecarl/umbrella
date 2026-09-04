# Test lab

Every fixture builds real repositories on disk: bare origins under `origins/`,
then working clones. Nothing is mocked, so a test runs the same git and jj a
user would.

`lab` gives two submodule projects and an umbrella that tracks them.
`git_checkout` and `jj_checkout` each clone that umbrella and initialise it in
one mode. `Checkout.cli()` runs the command line in process from inside the
checkout, and the hook tests call `git` itself, so the hooks run the way git
runs them.
