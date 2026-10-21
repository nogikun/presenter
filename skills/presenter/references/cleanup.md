# ローカルデータの片付け

presenterが手元に置くデータを、何を消してよいかと一緒に整理し、確かめてから消す手順。
`<home>`は`%LOCALAPPDATA%\presenter-tts`（環境変数`PRESENTER_TTS_HOME`があればその値）、`<presenter>`はpresenterスキルのフォルダ。

## 置いてあるもの

| 場所 | 中身 | 目安 | 消したら | 消してよいか |
|---|---|---|---|---|
| `<home>/cache/models/` | S2 Proのモデルとトークナイザー | 約3.4 GB | 次のセットアップで再ダウンロード（数分） | よい |
| `<home>/cache/s2-build/` | ビルドしたs2.cpp | 約0.3 GB | 再ビルド（約20分） | よい |
| `<home>/cache/s2.cpp/` | s2.cppのソース | 約0.1 GB | 再取得（ビルドと同時） | よい |
| `<home>/cache/fillers/` | 声ごとのフィラーのキャッシュ | 数MB | 次の`say`で自動で作り直す | よい |
| `<home>/cache/*.log`、`validation/`、`tmp/` | ログと診断結果 | 小さい | なし | よい |
| `<home>/voices/` | 登録した声（`.s2voice`）、参考音声、文字起こし、元の録音（`sources/`） | 数MB | **取り直せない**。録音と文字起こしから登録し直すしかない | **ユーザーが声ごとに明示したときだけ** |
| `<home>/config.toml` | 設定 | 小さい | 既定値に戻る | 確認してから |
| `<home>/api-key.txt` | OpenAI互換APIのキー | 小さい | 次の`api`で別のキーになる（接続中のアプリの設定が使えなくなる） | 確認してから |
| `<presenter>/tools/.venv/` | Pythonと依存パッケージ | 約50 MB | 次の`uv run`で自動で作り直す | よい |
| `<deck>/narration/audio/`、`narration.wav`、`timeline.json`、`player.html` | デッキごとの合成結果 | 1分あたり約5 MB | `narrate`で作り直せる（時間はかかる） | 確認してから（成果物なので） |
| `<作業フォルダ>/outputs/` | `say`・`speak`・`voice add`の出力 | 小さい | なし | 確認してから |

`<deck>/narration/script.json`と`checked/`はpresentation-jaの原稿なので、このスキルの片付けでは消さない。

## 手順

### 1. 何がどれだけあるかを調べる

```powershell
$h = if ($env:PRESENTER_TTS_HOME) { $env:PRESENTER_TTS_HOME } else { Join-Path $env:LOCALAPPDATA 'presenter-tts' }
Get-ChildItem -Force $h, (Join-Path $h 'cache') -ErrorAction SilentlyContinue | ForEach-Object {
  $bytes = if ($_.PSIsContainer) { (Get-ChildItem -Recurse -Force -File $_.FullName -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum } else { $_.Length }
  '{0,-32} {1,10:N1} MB' -f $_.FullName.Substring($h.Length + 1), ($bytes / 1MB)
}
uv run --project "<presenter>/tools" presenter-tts voice list
```

PowerShellの`$home`は予約済みの変数なので、別の名前（ここでは`$h`）を使う。

### 2. 消す範囲を決めて、ユーザーに見せる

ユーザーの目的から範囲を選び、**消すパス、容量、消したあとに起きること**を一覧にして確認を取る。確認なしに消さない。

| 目的 | 消す範囲 |
|---|---|
| 少し空けたい | `cache/fillers/`、ログ、`validation/`、`tmp/` |
| 大きく空けたいが、また使う | `cache/models/`（再ダウンロードは数分。ビルドは残す） |
| エンジンを作り直したい | `cache/s2-build/`と`cache/s2.cpp/`（その後presenter-setupで再ビルド） |
| 当面使わない | `cache/`をまるごと（声と設定は残る） |
| 完全に消したい | 下の「アンインストール」 |

声を消すのは、ユーザーが**どの声を消すかを名前で言ったとき**だけ。「全部消して」と言われても、声が含まれることを伝えて、もう一度確かめる。

### 3. TTSサーバーを止める

ビルドやモデルを使っているサーバーが動いていると、ファイルを消せない。

```powershell
uv run --project "<presenter>/tools" presenter-tts server stop
uv run --project "<presenter>/tools" presenter-tts server status
```

`listening`のままで「started elsewhere」と出たら、このツール以外が起動したs2.cppが動いている。ユーザーに止めてもらう。

### 4. 消す

確認した範囲だけを、パスを明示して消す。ワイルドカードで`<home>`の外まで広げない。

```powershell
Remove-Item -Recurse -Force (Join-Path $h 'cache\models')
```

声は専用のコマンドで消す（声のファイル、参考音声、文字起こし、その声のフィラーをまとめて消す）。

```powershell
uv run --project "<presenter>/tools" presenter-tts voice remove <名前> --yes
```

### 5. 消えたことと、残したものを確かめる

手順1をもう一度実行し、空いた容量と、残した声・設定を伝える。
エンジンやモデルを消した場合は、次に使う前にpresenter-setupが必要になることも伝える。

## アンインストール

1. サーバーを止める（手順3）。
2. 消すものをユーザーに見せる。**声・設定・APIキーも消えること**を明示して、確認を取る。声を残したい場合は、先に`<home>\voices`を別の場所へコピーしてもらう。
3. `<home>`をまるごと消す。

   ```powershell
   Remove-Item -Recurse -Force $h
   ```

4. `<presenter>/tools/.venv`を消す（スキル自体を残す場合も、次の`uv run`で作り直される）。
5. スキル自体を消す場合は、インストールした先のフォルダ（例: `.claude/skills/presenter/`）を消すか、`npx skills`の削除機能を使う。

CUDA Toolkit、Visual Studio、FFmpeg、uvなど、presenter-setupが入れた（または入っていた）システムのソフトは、ほかのソフトも使っている可能性がある。消すかどうかはユーザーが決める。このスキルからは消さない。

## 古い配置の名残

2026-10より前のpresenterリポジトリでは、エンジンとモデルをリポジトリ直下の`.runtime/`に、声を`voices/`に置いていた。
その頃のチェックアウトがある場合、`.runtime/s2-build-graphs/`（実験用のビルド、約0.3 GB）や`.runtime/s2-build-graphs.log`は使っていないので消してよい。
`.runtime/presentation-ja-workspace/`など、presentation-jaの開発用のフォルダは別スキルのものなので、このスキルの片付けでは触らない。
