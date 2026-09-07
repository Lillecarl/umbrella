{
  # Where every dependency lives, as directories. nix/sources.nix says how
  # this repository finds the nixidae umbrella that owns them.
  #
  # This is the tool that drives that umbrella, and it still takes its own
  # nixpkgs from it. There is no cycle: the umbrella resolves sources without
  # building anything.
  sources ? import ./nix/sources.nix,
  pkgs ? import sources.nixpkgs { },
}:
rec {
  umbrella = pkgs.python3Packages.callPackage ./package.nix { };
  shell = pkgs.callPackage ./shell-package.nix { inherit umbrella; };
}
