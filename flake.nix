# Everything here must be reachable without a flake entrypoint, so this file
# holds nothing but calls into default.nix.
{
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  outputs =
    inputs:
    let
      inherit (inputs.nixpkgs) lib;
      forEachSystem = lib.genAttrs lib.systems.flakeExposed;
    in
    {
      packages = forEachSystem (
        system:
        let
          pkgs = import inputs.nixpkgs { inherit system; };
          defaultNix = import ./. { inherit pkgs; };
        in
        {
          default = defaultNix.umbrella;
          inherit (defaultNix) umbrella;
        }
      );
      devShells = forEachSystem (
        system:
        let
          pkgs = import inputs.nixpkgs { inherit system; };
        in
        {
          default = (import ./. { inherit pkgs; }).shell;
        }
      );
    };
}
