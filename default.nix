{
  pkgs ? import <nixpkgs> { },
}:
rec {
  umbrella = pkgs.python3Packages.callPackage ./package.nix { };
  shell = pkgs.callPackage ./shell-package.nix { inherit umbrella; };
}
