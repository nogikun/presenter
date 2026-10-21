# CUDAとWSLの確認

2026-10-20の確認結果。Windows用とWSL用のToolkitは別々に管理する。
今回の自動セットアップはWindows版s2.cpp用。WSLへ移すだけで高速化するとは判断していない。

| 項目 | 確認結果 |
| --- | --- |
| GPU | RTX 3080、VRAM 10GB。確認時の空きは約5.7GB |
| Windowsドライバー | 610.47 |
| Windowsの`nvcc` | CUDA 11.8。s2.cppの要求する12.4以上を満たさない |
| `CUDA/v12.4` | フォルダーはあるがコンパイラー・ヘッダー・ライブラリーがなく、利用可能なToolkitではない |
| `%USERPROFILE%/.wslconfig` | `[wsl2]`、`memory=16GB` |
| WSLの状態・ディストリビューション | この実行環境では`Wsl/E_ACCESSDENIED`になり確認できない |

インストール・アンインストール・WSL設定変更はまだ実行していない。
この実行環境は管理者ではなく、外部DNSとWSL操作が制限されている。
この制限をWSL自体の故障と判断しない。

## WindowsのToolkitを更新する

1. [NVIDIA公式のCUDA 12.9 Update 1](https://developer.nvidia.com/cuda-12-9-1-download-archive?target_os=Windows&target_arch=x86_64&target_version=11&target_type=exe_local)を候補にする。s2.cppの要求12.4以上を満たし、既存ドライバー610.47を更新せずに試す構成。このリポジトリでのビルドは未検証で、MSVCとの適合は構成・ビルド時に確認する。13系が必要と判明したら、その根拠に合わせて選び直す。
2. カスタムインストールでToolkitとVisual Studio統合を選ぶ。GPUドライバー更新は別途必要性を判断する。
3. 新しいPowerShellで、インストールした実際のパスを指定して確認・ビルドする。以下は12.9を標準の場所へ入れた場合。

```powershell
.\tools\setup-s2.ps1 -CudaRoot 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9' -CheckOnly
.\tools\setup-s2.ps1 -CudaRoot 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9'
uv run --project "<presenter>/tools" presenter-tts server start
```

`-CheckOnly`はファイルとコンパイラーバージョンの確認で、MSVCとの組み合わせやGPU実行まで保証しない。
ビルド成功時に選択したToolkitの場所を記録し、`server start`はそのDLLをプロセス内のPATHで優先する。永続的なPATHは変更しない。
同じビルドディレクトリを異なるToolkitへ変更してCMakeが拒否した場合は、既存ビルドを別名で保管してから再構成する。

11.8の削除はユーザー許可済み。新しい版でビルドと音声生成を確認してから、Windowsの「インストールされているアプリ」で11.8のToolkit関連コンポーネントをアンインストールする。
GPUドライバーは残す。フォルダーを直接削除しない。
既存プロジェクトが11.8のコンパイラーを参照していないか確認し、削除後にPATHと`CUDA_PATH`を確認する。

## 検証結果をまとめて保存する

通常のPowerShellで以下を実行する（`<presenter>`はpresenterスキルのフォルダ）。モデルのダウンロード・インストール・WSL停止は行わない。

```powershell
uv run --project "<presenter>/tools" python "<presenter>/tools/scripts/verify_local.py"
```

テスト、SSML解析、環境診断、WSLの状態を`<home>/cache/validation/<日時>/report.json`へ保存する。
依存関係は`uv run --project`が初回に`<presenter>/tools/.venv`へそろえる。
`checks_passed`がtrueでも、doctorに記録された通信や依存関係の警告・テストのスキップを確認する。実機での音声合成成功とは区別する。

Toolkit導入とセットアップ後、`uv run --project "<presenter>/tools" presenter-tts server start`でTTSサーバーを起動してから、以下を実行する。

```powershell
uv run --project "<presenter>/tools" python "<presenter>/tools/scripts/verify_local.py" --synthesize --input "<presenter>/tools/examples/latency.ssml" --runs 3
uv run --project "<presenter>/tools" python "<presenter>/tools/scripts/verify_local.py" --synthesize --input "<presenter>/tools/examples/presentation.ssml"
```

音声は`outputs/validation-<日時>/`へ保存する。再生せずに最初のPCMまでの時間とRTFを測る。
最初の試行は初回処理の影響を含む可能性があるため、2・3回目とも比較する。
速度だけでなく、保存した音声で読み抜け、音切れ、声の一貫性、間、強調を聴いて確認する。
10層・18層・36層などを比較するときは、`server stop`で止めてから`<home>/config.toml`の`[server] gpu_layers`を変えて起動し直す。同時に複数起動しない。

## WSLの確認

通常のPowerShellで次を実行する。設定の変更や停止は行わない。

```powershell
wsl --version
wsl --status
wsl --list --verbose
Get-Content "$env:USERPROFILE\.wslconfig"
```

使用するディストリビューションがWSL **2**であることを確認する。
必要なら`wsl --update`でWSLを更新する。WSL内で、presenterスキルのフォルダを指定して読み取り専用診断を実行する。

```bash
bash "/mnt/c/<presenterスキルのフォルダ>/tools/scripts/check-wsl.sh"
```

診断スクリプトのWSL上での実行は未検証。この実行環境ではWSLとGit Bashの起動が拒否されており、Bashによる構文検証も実施できていない。

WSLでs2.cppをビルドする場合:

- NVIDIAの[WSLガイド](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)に従い、Linux用CUDA Toolkitを用意する。GPUドライバーはWindows側を共有する。WSL内へLinux用NVIDIAドライバーを入れない。
- WSL-Ubuntu用のインストーラーか、`cuda-toolkit-X-Y`パッケージを選ぶ。ドライバーを含む`cuda`、`cuda-X-Y`、`cuda-drivers`を選ばない。Windowsの11.8を消してもLinuxのToolkitは更新されない。
- Linux側の`nvcc --version`が12.4以上で、`nvidia-smi`がGPUを認識することを確認する。PATHにWindowsの`nvcc.exe`が混ざっていないか確認する。
- ソース・モデル・ビルド出力はLinuxのホーム配下に置く。Windows版と同じCMakeビルドディレクトリを共用しない。ビルド方法は[s2.cppの上流手順](https://github.com/rodrigomatta/s2.cpp)に従う。
- Linux側のサーバーは`127.0.0.1:3030`に限定して起動する。Windows側のコントローラーから`uv run --project "<presenter>/tools" presenter-tts doctor`で到達性を確認する。localhost転送は既定で有効。届かない場合はWSLバージョンとネットワーク設定を確認してから変更する。

`memory=16GB`はWSL全体のシステムRAM上限で、GPUのVRAMを増やす設定ではない。
今回のQ4モデルでまず維持し、Linux側の`free -h`やOOMログを確認してから調整する。
物理RAMの余裕を確認できていないため、上限引き上げやswap増量を自動では行わない。swapはVRAM不足による遅延の解消策にはならない。
`processors`やネットワークモードも、診断結果なしに固定しない。

`.wslconfig`の変更反映にはWSLの停止・再起動が必要になる。
`wsl --shutdown`は全ディストリビューションを止めるため、Dockerや実行中の処理を保存してから行う。

参考: [MicrosoftのWSL詳細設定](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)、[NVIDIA Windowsインストールガイド](https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/index.html)。
