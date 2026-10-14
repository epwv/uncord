{
  description = "Discord in your terminal, with voice calls, inline images and screen sharing";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = pkg: (pkg.pname or "") == "uncord";
      }));
    in
    {
      packages = forAll (pkgs: rec {
        uncord = pkgs.callPackage ./packaging/nix/package.nix { };
        default = uncord;
      });

      apps = forAll (pkgs: rec {
        uncord = { type = "app"; program = "${self.packages.${pkgs.system}.uncord}/bin/uncord"; };
        default = uncord;
      });

      overlays.default = final: _prev: { uncord = final.callPackage ./packaging/nix/package.nix { }; };

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ self.packages.${pkgs.system}.uncord ];
          packages = [ pkgs.python3Packages.pyflakes ];
        };
      });
    };
}
