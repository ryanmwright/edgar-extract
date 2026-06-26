{
  description = "Fund X-Ray Python pipeline environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;
      in {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.uv
          ];

          # pip wheels for numpy/pyarrow/lxml expect libstdc++, libz, etc.
          # on standard library paths — NixOS has them in /nix/store only.
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];

          shellHook = ''
            if [ ! -d .venv ]; then
              echo "Creating .venv with uv..."
              uv venv --python ${python}/bin/python .venv
            fi
            source .venv/bin/activate
            if [ ! -f .venv/.synced ] || [ requirements.txt -nt .venv/.synced ]; then
              echo "Installing dependencies..."
              uv pip install --python .venv/bin/python -r requirements.txt
              touch .venv/.synced
            fi
            echo "Fund X-Ray dev shell — $(python --version)"
            if [ -z "$EDGAR_USER_AGENT" ]; then
              echo "WARN: EDGAR_USER_AGENT is not set. SEC EDGAR requires a"
              echo "      descriptive User-Agent (e.g. 'Fund X-Ray you@example.com')."
              echo "      Export it before running the pipeline."
            fi
          '';
        };
      });
}
