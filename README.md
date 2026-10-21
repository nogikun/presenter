# presenter-tts

量子化したFish Audio S2 Pro（[s2.cpp](https://github.com/rodrigomatta/s2.cpp)）を手元のGPUで動かし、
日本語の文章やSSMLの発表原稿を読み上げるツールです。Windows向け（RTX 3080で確認）。

- `say`: 打った文章をその場で読み上げる（長い文は区切って生成、待ち時間にはフィラー）
- `voice`: 自分の声などを録音からクローンして登録する
- `speak` / `plan`: SSMLの原稿をWAVにする
- `api`: OpenAI互換の音声合成API（`POST /v1/audio/speech`）を立てる
- `server`: TTSエンジンを裏で起動・停止する

## はじめかた

[uv](https://docs.astral.sh/uv/)のプロジェクトです。

```powershell
uv sync
Copy-Item presenter.example.toml presenter.local.toml
.\tools\setup-s2.ps1
uv run presenter-tts say こんにちは。
```

`setup-s2.ps1`はs2.cppを取得してCUDAでビルドし、モデル（約3.6 GB）をダウンロードします。
必要なもの（Visual Studio 2022、CUDA Toolkit 12.4以上、FFmpegなど）は[ローカル音声生成の手順](docs/tts.md)を参照してください。

- 普段の使い方: [docs/usage.md](docs/usage.md)
- セットアップ、SSMLの対応範囲、検証状況: [docs/tts.md](docs/tts.md)
- CUDAとWSLの確認: [docs/tts-cuda-wsl.md](docs/tts-cuda-wsl.md)

## ファイルの置き場所

| 場所 | 中身 | git |
|---|---|---|
| `.runtime/` | s2.cpp、ビルド、モデル、ログ、キャッシュ。消しても`setup-s2.ps1`で取り直せる | 入れない |
| `voices/` | 登録した声と元の録音。取り直せないので消さない | 入れない |
| `outputs/` | 生成した音声 | 入れない |
| `presenter.local.toml` | 自分の設定（`presenter.example.toml`をコピー） | 入れない |

容量を空けたいときは`.runtime/models/`（または`.runtime/`ごと）を消してかまいません。

## 声のクローンについて

自分の声か、本人から許可を得た声だけを登録してください。

## ライセンス

このリポジトリのコードは[MITライセンス](LICENSE)です。

s2.cppとFish Audio S2 Proのモデルは同梱していません。`setup-s2.ps1`が利用者の環境に取得します。
これらは**Fish Audio Research License**で提供されており、研究・非商用の利用は無償、商用利用にはFish Audioとの別途の契約が必要です。
詳細は取得後の`.runtime/s2.cpp/LICENSE.md`を参照してください。

> This model is licensed under the Fish Audio Research License, Copyright © 39 AI, INC. All Rights Reserved.
