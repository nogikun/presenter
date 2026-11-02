---
name: presenter-setup
description: presenterスキル（ローカルのFish Audio S2 Pro音声合成）をWindows/CUDAまたはApple Silicon Mac/Metalで使える状態にする。前提ソフトの確認と導入、ライセンス確認、s2.cppのビルド、モデル取得、動作確認、必要なら声の登録までを進める。「presenterをセットアップして」「音声合成を使えるようにして」「TTSのエンジンを入れて」「doctorでエラーが出た」「ビルドし直して」で使う。
license: MIT
compatibility: Windows 10/11＋NVIDIA GPU/CUDA 12.4以上、またはApple Silicon Mac＋Metalに対応。uv、Git、FFmpeg、CMake、WindowsはVisual Studio C++、MacはXcode Command Line Tools、GitHub/Hugging Face接続が必要。presenterスキルと同じ場所にインストールされていること。
---

# presenter-setup

presenterのエンジン（s2.cpp）とモデルを用意し、音が出るところまで確かめる。
ユーザーにやってもらうのは、**ソフトの導入とライセンスへの同意の承認**だけにする。それ以外のコマンドはこちらで実行する。

## 0. presenterスキルを見つける

このスキルのフォルダの隣にある`presenter/`（例: `.claude/skills/presenter/`）を`<presenter>`とする。
`<presenter>/tools/pyproject.toml`がなければ止めて、presenterスキルも入れるよう伝える（`npx skills add`で両方入れる）。
コマンドはすべて`uv run --project "<presenter>/tools" presenter-tts ...`で呼ぶ。パスは引用符で囲む。

## 1. いまの状態を調べる

```console
uv run --project "<presenter>/tools" presenter-tts doctor
```

uv自体がない場合はこのコマンドが動かないので、先に手順2でuvを入れる。
出力のJSONから次を読む。**`engine.ready`がtrueなら手順5（動作確認）へ飛ぶ。**

| 項目 | 必要な状態 |
|---|---|
| `prerequisites.platform` | `windows`または`macos`。それ以外は未対応 |
| Macの`prerequisites.architecture` | Apple Siliconを示す`arm64` |
| Windowsの`gpu` | NVIDIAのGPUがあり、合計のVRAM（2つ目の値、MB）が6000以上 |
| Windowsの`prerequisites.cuda_version` | 12.4以上 |
| Windowsの`prerequisites.visual_studio_cpp` | パスがある（C++のビルドツールが入っている） |
| Macの`prerequisites.metal` | `xcrun --find metal`で見つかる |
| `prerequisites.git`、`prerequisites.uv` | パスがある |
| `ffmpeg`、`ffplay` | パスがある |
| `huggingface.co`、`github.com` | `DNS available` |

WindowsでGPUがない、またはVRAMが足りない場合は、セットアップを進めずにその旨を伝える（CPUだけでは実用的な速度にならない）。MacはApple SiliconとMetalが必要。

## 2. 足りないソフトを入れる

足りないものだけを一覧にし、**何を・どこから・どれくらいの容量で入れるか**を見せて、承認を得てから入れる。
大きなもの（Visual Studio、CUDA）は管理者権限が必要で、ダウンロードも数GBある。承認なしに入れない。

Windows:

| ソフト | 導入コマンド | 備考 |
|---|---|---|
| uv | `winget install --id astral-sh.uv -e` | |
| Git | `winget install --id Git.Git -e` | |
| FFmpeg（FFplayを含む） | `winget install --id Gyan.FFmpeg -e` | |
| Visual Studio 2022 C++ビルドツール | `winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"` | CMakeも含まれる。数GB |
| CUDA Toolkit | `winget show --id Nvidia.CUDA --versions`で版を確かめ、12.4以上を`winget install --id Nvidia.CUDA -e --version <版>` | 約3 GB。GPUドライバーの更新は別に判断する |

- 導入後は、新しく開いたシェルでないとPATHが反映されないことがある。もう一度`doctor`を実行し、見つからない場合はフルパスで確かめる。
- CUDAが複数入っていて、PATHの先頭が古い版（11.xなど）のことがある。`C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\`の下で12.4以上の最も新しい版を選び、手順4で`-CudaRoot`に渡す。PATHは書き換えない。
- CUDAやWSLの細かい確認は[cuda-wsl.md](references/cuda-wsl.md)を読む。

Mac:

| ソフト | 導入コマンド | 備考 |
|---|---|---|
| Xcode Command Line Tools | `xcode-select --install` | GUIでの承認と完了待ちが必要。Git、clang++、Metalを含む |
| uv | `brew install uv` | Homebrewがない場合は、公式インストーラーを提示して承認を得る |
| FFmpeg（FFplayを含む） | `brew install ffmpeg` | Homebrewがない場合は、先に導入方法をユーザーと決める |

`xcode-select -p`、`xcrun --find clang++`、`xcrun --find metal`を確認する。CMakeがなければ`setup-s2.sh`がuvxで一時的に用意するため、CMakeだけをHomebrewへ追加しない。

## 3. ライセンスに同意をもらう

s2.cppのエンジンとS2 Proのモデルは**Fish Audio Research License**で提供される。ダウンロードの前に、次をユーザーに伝えて同意を得る。

- 研究・非商用の利用は無償。**商用利用にはFish Audioとの別途の契約が必要**（business@fish.audio）
- 配布するときはライセンス文と帰属表示（"This model is licensed under the Fish Audio Research License, Copyright © 39 AI, INC. All Rights Reserved."）が必要
- 全文は取得後の`<home>/cache/s2.cpp/LICENSE.md`

同意がなければ、ここで止める。

## 4. エンジンをビルドし、モデルを取得する

`doctor`の`prerequisites.platform`に従い、対応する片方だけで確認する（数秒）。

```powershell
& "<presenter>/tools/scripts/setup-s2.ps1" -CheckOnly -CudaRoot "<CUDAのフォルダ>"
```

```bash
"<presenter>/tools/scripts/setup-s2.sh" --check-only
```

確認結果がreadyなら、本番を**バックグラウンドで**実行する。s2.cppの取得とビルドに約20分、モデルのダウンロードに数分かかる。始める前に目安の時間を伝える。

```powershell
& "<presenter>/tools/scripts/setup-s2.ps1" -CudaRoot "<CUDAのフォルダ>"
```

```bash
"<presenter>/tools/scripts/setup-s2.sh"
```

- 置き場所は`%LOCALAPPDATA%\presenter-tts\cache\`（Windows）または`~/.local/share/presenter-tts/cache/`（Mac）。`PRESENTER_TTS_HOME`があればその下。どのプロジェクトからも共有される。
- Windowsのビルド中に大量に出る`warning C4819`（文字コード）や`C4244`（型変換）は無害。どちらのOSも終わりに`Setup finished`が出れば成功。
- 失敗したら、出力の`error`を含む行を探す。WindowsはCUDA、MacはXcode/Metal/CMakeを確認する。同じビルド先を別のバックエンドで構成し直して拒否された場合は、`cache/s2-build`を消してからやり直す（ユーザーに確認してから）。
- モデルのダウンロードだけが失敗した場合、ビルドは残っている。もう一度同じコマンドを実行すればダウンロードから再開する。

設定ファイルがなければ、見本をコピーする（既存のものは上書きしない）。

```powershell
$ttsHome = if ($env:PRESENTER_TTS_HOME) { $env:PRESENTER_TTS_HOME } else { Join-Path $env:LOCALAPPDATA 'presenter-tts' }
$config = Join-Path $ttsHome 'config.toml'
if (-not (Test-Path -LiteralPath $config)) {
    New-Item -ItemType Directory -Path $ttsHome -Force | Out-Null
    Copy-Item "<presenter>/tools/presenter.example.toml" -Destination $config
}
```

Macでは同じ見本を`~/.local/share/presenter-tts/config.toml`へ置く。既存ファイルは上書きしない。

```bash
tts_home="${PRESENTER_TTS_HOME:-$HOME/.local/share/presenter-tts}"
mkdir -p "$tts_home"
test -e "$tts_home/config.toml" || cp "<presenter>/tools/presenter.example.toml" "$tts_home/config.toml"
```

## 5. 音が出るか確かめる

```console
uv run --project "<presenter>/tools" presenter-tts doctor
uv run --project "<presenter>/tools" presenter-tts say "こんにちは。音声合成のテストです。"
```

`engine.ready`がtrueで、`say`がエラーなく終わったら、**ユーザーに聞こえたかを尋ねる**。
生成も再生も成功しているのに聞こえない場合は、OSの出力デバイスとミュートを確かめてもらう（よくある原因）。
サーバーの起動に失敗したら`<home>/cache/s2-server.log`の末尾を読む。GPUメモリまたはMacのユニファイドメモリ不足なら、`config.toml`の`[server] gpu_layers`を減らす（例: 18）。

確かめ終わったら、続けて使う予定がなければサーバーを止める（GPUのメモリを約4 GB使うため）。

```console
uv run --project "<presenter>/tools" presenter-tts server stop
```

## 6. 声を登録する（任意）

既定の声はモデル自身の声で、文ごとに声質が変わることがある。発表に使うなら、声を1つ登録しておくとよい。
ユーザーが望んだ場合だけ、presenterスキルの「声を登録する」の手順に従う（本人の声か許可を得た声だけ）。
登録したら、`config.toml`の先頭の`voice = "<名前>"`を有効にすると既定の声になる。

## 7. 報告する

次をまとめて伝える。

- 入れたソフトと版、ビルドしたバックエンド（CUDAまたはMetal）
- 置き場所（Windowsは`%LOCALAPPDATA%\presenter-tts`、Macは`~/.local/share/presenter-tts`）と使った容量
- 動作確認の結果（聞こえたかどうかを含む）
- 次にできること（presenterでスライドの音声を作る、`say`で話させる）と、片付けの方法（presenterの`references/cleanup.md`）

## やり直すとき

- CUDA/Xcodeを更新した、ビルドが壊れた: `cache/s2-build`を消して手順4から。
- モデルを消した: 手順4をもう一度実行する（ビルドは使い回され、ダウンロードだけが走る）。
- 別のPCに移る: そのPCでこのスキルを最初から実行する。声は`<home>/voices/`をコピーすれば持っていける。
