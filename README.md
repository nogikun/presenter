# presenter

スライドと発表原稿から、ナレーション付きの発表（解説動画の音声）を手元のGPUで作るAgent Skill集です。
音声合成は量子化したFish Audio S2 Pro（[s2.cpp](https://github.com/rodrigomatta/s2.cpp)）をローカルで動かします。Windows向け（RTX 3080で確認）。

```
html-deck（スライド） → presentation-ja（原稿の作成と検査） → presenter（音声とプレーヤー）
                                                              ↑ presenter-setup（エンジンの準備）
```

| スキル | 役割 |
|---|---|
| [`presentation-ja`](skills/presentation-ja/SKILL.md) | 聞き手・目的・資料・持ち時間から日本語の発表原稿を作り、検査する。html-deckのスライドごとに原稿を紐づける |
| [`presenter`](skills/presenter/SKILL.md) | 検査済みの原稿から、スライドごとの音声、つなげた音声、タイムライン、スライドと同期して再生する`player.html`を作る。声のクローン、入力した文章の読み上げ、OpenAI互換の音声APIも |
| [`presenter-setup`](skills/presenter-setup/SKILL.md) | 前提ソフトの確認と導入、ライセンスの確認、s2.cppのビルド、モデルの取得、動作確認を、エージェントが進める |

スライドを作る`html-deck`は別のリポジトリのスキルです。

## はじめかた

```sh
npx skills add nogikun/presenter
```

入れたあとはエージェントに頼みます。

1. 「presenterをセットアップして」 → presenter-setupが環境を調べ、必要なものを（承認を得てから）入れ、エンジンをビルドします（初回は約30分）
2. 「html-deckでスライドを作って、presentation-jaで原稿も作って」
3. 「そのスライドに私の声でナレーションを付けて」 → presenterが`<deck>/narration/player.html`などを作ります

presenterのコードは`skills/presenter/tools/`（uvのプロジェクト）にあり、`uv run --project "<presenter>/tools" presenter-tts ...`で直接使うこともできます。
コマンドの一覧は[commands.md](skills/presenter/references/commands.md)を参照してください。

## ファイルの置き場所

エンジン・モデル・声はユーザー共通の`%LOCALAPPDATA%\presenter-tts`に置き、どのプロジェクトからも共有します（環境変数`PRESENTER_TTS_HOME`で変更可）。スキルを更新しても消えません。

| 場所 | 中身 | 消してよいか |
|---|---|---|
| `cache/` | s2.cpp、ビルド、モデル（約3.6 GB）、ログ、フィラー | よい（presenter-setupで取り直せる） |
| `voices/` | 登録した声と元の録音 | 消さない（取り直せない） |
| `config.toml`、`api-key.txt` | 設定とAPIキー | 確認してから |

片付けの手順は[cleanup.md](skills/presenter/references/cleanup.md)にあります（エージェントに「presenterを片付けて」と頼めます）。

## 声のクローンについて

自分の声か、本人から許可を得た声だけを登録してください。

## 開発

- presenterのテスト: `uv run --project skills/presenter/tools python -m unittest discover -s skills/presenter/tools/tests`
- presentation-jaの評価: [評価アーキテクチャ](docs/presentation-skill-architecture.md)、[検証結果](dev/presentation-ja/validation.md)
- presenterの評価課題: [dev/presenter/evals.json](dev/presenter/evals.json)

ローカルから導入する場合は `npx skills add . --skill presenter` のように実行してください。

## ライセンス

このリポジトリのコードは[MITライセンス](LICENSE)です。

s2.cppとFish Audio S2 Proのモデルは同梱していません。presenter-setup（`setup-s2.ps1`）が利用者の環境に取得します。
これらは**Fish Audio Research License**で提供されており、研究・非商用の利用は無償、商用利用にはFish Audioとの別途の契約が必要です。
詳細は取得後の`%LOCALAPPDATA%\presenter-tts\cache\s2.cpp\LICENSE.md`を参照してください。

> This model is licensed under the Fish Audio Research License, Copyright © 39 AI, INC. All Rights Reserved.
