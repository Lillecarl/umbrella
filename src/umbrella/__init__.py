"""Drive an umbrella git repo whose submodules are separate projects.

The umbrella is always plain git. jj ignores gitlinks, so a jj umbrella can
never record a submodule pointer.

Each checkout picks how it drives the submodules. Plain git is the default.
`umbrella jjinit` colocates them and writes a marker inside .git, so the choice
stays local: one person can use jj while another uses git on the same project.

Division of labour:

- libgit2, through pygit2, does every local read and write on the umbrella.
  The guards ask git questions only, so they behave the same in both modes.
- The jj CLI drives a submodule in jj mode, including its fetch and push.
- The git CLI drives a submodule in git mode, and does the umbrella's own
  network calls, which is where libgit2 would need credentials.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
