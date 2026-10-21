---
name: presenter
description: html-deckで作ったスライドとpresentation-jaで検査した発表原稿から、ローカルのFish Audio S2 Pro（s2.cpp）で解説動画・ナレーション付きスライド用の音声を合成し、スライドごとのWAV、つなげたWAV、タイムライン、スライドと音声を同期して再生できるplayer.htmlを作る。「スライドに音声を付けて」「発表原稿を読み上げて」「ナレーションを作って」「解説動画の音声」「自分の声で読ませたい」「声をクローンして登録」と言われたら、音声合成という語がなくても使う。入力した文章をその場で読ませる、SSML原稿のWAV化、OpenAI互換の音声API、TTSサーバーの起動・停止、ローカルデータの片付けにも使う。原稿の執筆はpresentation-ja、スライド作成はhtml-deck、初回の環境構築はpresenter-setupが担当する。
license: MIT
compatibility: Windows 10/11とNVIDIA GPU（VRAM 6 GB以上、CUDA Toolkit 12.4以上）、uv、FFmpeg/FFplayが必要。初回はpresenter-setupスキルでs2.cppのビルドとモデル（約3.6 GB）の取得を行う（ネットワーク接続が必要）。合成自体はローカルで完結する。s2.cppとモデルはFish Audio Research License（非商用）。
---

# presenter

スライドと発表原稿を、聞ける形（音声とプレーヤー）にする。
動画の書き出しはこのスキルの範囲外で、そのための音声とタイムラインまでを作る。

```
html-deck（スライド） → presentation-ja（原稿の作成と検査） → presenter（音声とプレーヤー）
```

コードは同梱の`tools/`（uvのプロジェクト、パッケージ名`presenter_tts`）にある。
`<presenter>`をこのSKILL.mdのあるフォルダとして、すべて次の形で呼ぶ。パスに空白が入ることがあるので引用符で囲む。

```powershell
uv run --project "<presenter>/tools" presenter-tts <command> ...
```

初回の呼び出しでuvが依存関係をそろえる。利用者に`uv sync`を叩かせない。

## 1. 動く状態か確かめる

```powershell
uv run --project "<presenter>/tools" presenter-tts doctor
```

`engine.ready`がfalseなら、ここで止めてpresenter-setupスキルに従う（ビルドとモデル取得、ライセンスの確認）。
TTSサーバーは`narrate`・`say`・`speak`・`api`が必要なときに自動で起動するので、手動で起動しなくてよい。

## 2. スライドの音声を作る

### 入力をそろえる

`<deck>`（html-deckのデッキ）に、presentation-jaの成果物があることを確かめる。

| ファイル | 作るスキル |
|---|---|
| `<deck>/presentation-map.json` | presentation-ja（`html_deck_bridge.py sync`） |
| `<deck>/narration/script.json` | presentation-ja |
| `<deck>/narration/checked/receipt.json` | presentation-ja（`presentation_check.py check`） |

足りなければ、ここで原稿を書かない。presentation-jaに渡す（スライドがなければhtml-deckから）。
原稿の正しさや聞きやすさはpresentation-jaの検査と評価が担当し、このスキルは合成だけを担当する。
音声化の前に、presentation-jaの`verify`を通しておくと、検査後に原稿が変わっていないことも確かめられる。

### 合成する

```powershell
uv run --project "<presenter>/tools" presenter-tts narrate "<deck>"
```

- 原稿・マップ・スライドのHTMLが、presentation-jaが検査したときと一致しなければ止まる。そのときはpresentation-jaでマップの同期と`check`/`verify`をやり直す。ハッシュだけを書き換えて通さない。
- 声は設定の既定の声を使う。別の声なら`--voice <名前>`。声の候補は`voice list`で確かめる。
- 生成は実時間より遅い（RTX 3080で約1.4倍）。10分の発表なら15分前後かかるので、**バックグラウンドで実行し**、始める前に目安の時間を伝える。進捗はスライドごとに標準エラーへ出る。
- 2回目以降は、本文・間・声・設定が変わったスライドだけを作り直す。全部作り直すときだけ`--force`。

### 確かめて渡す

できるもの（`<deck>/narration/`）:

| ファイル | 用途 |
|---|---|
| `player.html` | スライドと音声を同期して再生する。ダブルクリックで開ける。←→でスライド移動 |
| `narration.wav` | 全スライドをつなげた音声。動画編集にそのまま使える |
| `timeline.json` | スライドごとの開始・終了秒。動画のカット位置に使える |
| `audio/<スライドID>.wav` | スライドごとの音声 |

渡す前に`player.html`を開き（`Invoke-Item`）、最初と最後のスライド、長いスライドを少なくとも聞く。
読み間違い（数字・英字・固有名詞）、不自然な切れ目、音量の差を見つけたら、原稿側の表記を直してpresentation-jaで再検査し、`narrate`をもう一度実行する（直したスライドだけ作り直される）。
返すときは、ファイルの場所、合計の長さ、使った声、聞いて確かめた範囲と、まだ聞いていない範囲を伝える。

## 3. 声を登録する

```powershell
uv run --project "<presenter>/tools" presenter-tts voice add <名前> "<録音>" --text "<録音で話している内容>"
uv run --project "<presenter>/tools" presenter-tts voice list
```

- **本人の声か、本人の許可を得た声だけを登録する。** 許可が分からない他人の声（配信の切り抜きなど）は、登録する前に確認する。
- 録音は5〜30秒で、BGMや他人の声が入っていない区間にする。長い録音は話している区間を切り出し、候補をユーザーに聞いてもらって選ぶ。
- 文字起こしは録音とそのまま一致させる。分からない部分を推測で埋めない。ユーザーに書き起こしてもらうか、聞き取れた範囲だけにする。
- 作り直しは`--force`。失敗しても前の声は残る。

声はユーザー共通の`%LOCALAPPDATA%\presenter-tts\voices\`に入り、どのプロジェクトからも名前で使える。

## そのほかの使い方

[commands.md](references/commands.md)を読む。

- `say`: 入力した文章をその場で読み上げる（長い文は区切って生成し、遅いときだけフィラーを入れる）
- `speak` / `plan`: SSMLの原稿をWAVにする（対応範囲は[engine.md](references/engine.md)）
- `api`: OpenAI互換の音声API（`POST /v1/audio/speech`）。他の機器に公開するときはAPIキーが必須
- `server start / status / stop`: TTSサーバーを裏で起動・停止する

## 片付け

ディスクを空けたい、使わなくなった、作り直したいと言われたら[cleanup.md](references/cleanup.md)の手順に従う。
キャッシュ（エンジン・モデル）は消しても取り直せるが、声・設定・APIキーは取り直せないので、消す前に必ず確認する。

## 気をつけること

- 使い終わったらTTSサーバーを止める（`server stop`）。動いている間はGPUのメモリを約4 GB使う。ユーザーが続けて使う場合は残してよい。
- 原稿の本文にSSMLやモデル固有のタグを足さない。読み上げ本文はpresentation-jaの`text`をそのまま使う。
- 合成した声を公開・配布する場合は、合成音声であることを示すかどうかをユーザーに確かめる。
- 生成に失敗したときは、エラーの1行目と`%LOCALAPPDATA%\presenter-tts\cache\s2-server.log`の末尾を読んでから対処する。原因が分からないまま再試行を繰り返さない。

## 同梱物

| パス | 中身 | 読むとき |
|---|---|---|
| `references/commands.md` | 全コマンドと設定の一覧 | `say`・`speak`・`api`・設定を使うとき |
| `references/engine.md` | エンジンの仕組み、SSMLの対応範囲、検証結果、遅いときの調べ方 | SSMLや速度の問題を扱うとき |
| `references/cleanup.md` | ローカルデータの片付け手順 | 片付け・アンインストール |
| `tools/` | presenter-tts本体（`src/presenter_tts`）、テスト、`presenter.example.toml` | 実行・改修 |
| `tools/scripts/` | s2.cppのビルドとモデル取得（presenter-setupが使う） | セットアップ |
