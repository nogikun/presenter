{
  description = "Odekake development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          # 全OS共通で必要なツール
          buildInputs = [
            pkgs.mise      # ランタイム管理（Node / Python 等）
            pkgs.direnv    # 環境自動切り替え
            pkgs.go-task   # タスクランナー（Taskfile.yml）
          ];

          shellHook = ''
            # miseの有効化（現在のシェルに合わせて自動検出）
            # .envrc の `use flake` 経由で自動的に呼び出される
            eval "$(mise activate zsh 2>/dev/null || mise activate bash)"
          '';
        };
      });
}