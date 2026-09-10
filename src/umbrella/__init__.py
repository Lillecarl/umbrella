"""Drive a repo whose nix/sources.lock names other projects.

The lock is the whole record. A source resolves from it, or from a working
copy beside the umbrella when there is one, and nothing about that working copy
is written down anywhere: it is an ordinary clone in an ignored directory.

There used to be a submodule per project, and a gitlink recording it. Two
records for one revision drift, and a gitlink forces the umbrella onto plain
git, because jj ignores gitlinks. Both problems go away with the submodules.

Each checkout picks how it drives the working copies. Plain git is the default.
`umbrella init --jj` colocates them and writes a marker inside .git, so the
choice stays local: one person can use jj while another uses git on the same
project.

Division of labour:

- libgit2, through pygit2, does every local read on the umbrella and on the
  working copies. The guards ask git questions only, so they behave the same
  in both modes.
- The jj CLI drives a working copy in jj mode, including its fetch and push.
- The git CLI drives one in git mode, and does every network call, which is
  where libgit2 would need credentials.
- The nix CLI reads nix/sources.nix and prefetches, because only nix can read
  a Nix file. `update` and `land` need it; nothing else does.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
