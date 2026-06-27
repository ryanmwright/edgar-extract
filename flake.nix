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
            # Auto-load secrets from a local, gitignored .env file so
            # EDGAR_USER_AGENT (and optionally OPENFIGI_API_KEY) don't
            # have to live in shell rc files. Copy .env.example → .env
            # to get started.
            if [ -f .env ]; then
              set -a
              source .env
              set +a
            fi

            echo "Fund X-Ray dev shell — $(python --version)"
            if [ -z "$EDGAR_USER_AGENT" ]; then
              echo "WARN: EDGAR_USER_AGENT is not set."
              echo "      Copy .env.example to .env and fill in your contact,"
              echo "      or export EDGAR_USER_AGENT before running."
            fi
          '';
        };
      });
}
