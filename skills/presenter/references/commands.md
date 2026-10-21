# presenter-tts のコマンド

`<presenter>`はこのスキルのフォルダ、`<home>`は`%LOCALAPPDATA%\presenter-tts`（環境変数`PRESENTER_TTS_HOME`で変更可）。
エンジンやモデルの準備は presenter-setup スキル（[エンジンの詳細](engine.md)）が行う。

## 0. 呼び方

すべて`uv run --project "<presenter>/tools" presenter-tts ...`で呼ぶ。どのフォルダから実行してもよい。
初回の呼び出しで、uvが`<presenter>/tools/.venv`にPython 3.11とTyperを`uv.lock`のとおり入れる。`uv sync`は不要。
設定は`<home>/config.toml`（なければ既定値）。別の設定を使うときは`--config <file>`をコマンド名の直後に付ける。

| 場所 | 中身 | 消してよいか |
|---|---|---|
| `<home>/cache/` | s2.cpp、ビルド、モデル、ログ、フィラー | よい（presenter-setupで取り直せる） |
| `<home>/voices/` | 登録した声と元の録音 | 消さない（取り直せない） |
| `<home>/config.toml`、`<home>/api-key.txt` | 設定とAPIキー | 消さない |

片付けの手順は[cleanup.md](cleanup.md)。

## 1. TTSサーバー

`say`と`speak`は、S2 ProのTTSサーバー（s2.cpp）に音声を作らせる。

```powershell
uv run --project "<presenter>/tools" presenter-tts server start
uv run --project "<presenter>/tools" presenter-tts server status
uv run --project "<presenter>/tools" presenter-tts server stop
```

- `start`はサーバーを裏で（別プロセスで）起動し、リクエストを受け付けられるまで待ってから戻る。ターミナルを閉じても動き続ける。
- `say`と`speak`は、サーバーが止まっていれば自動で起動する。自動起動しない場合は設定の`[server] auto_start = false`。
- 動いている間はGPUメモリを約4.6 GB使う。使い終わったら`stop`で止める。
- `stop`で止まるのは、このツールが起動したサーバーだけ。s2.cppを手動で起動した場合は、そのターミナルでCtrl+Cを押して止める。
- GPUに載せる層の数やコーデックの置き場所は、設定の`[server]`（`gpu_layers`、`codec_gpu`）で変える。
- 起動時の出力は`<home>/cache/s2-server.log`に残る。起動に失敗したときは、このログの末尾も表示する。

## 2. 話させる（say）

```powershell
uv run --project "<presenter>/tools" presenter-tts say
```

`> `が出たら文字を入力してEnter。生成しながら再生する。

| 入力 | 動作 |
|---|---|
| 文章 | 読み上げる。最後の音声は`outputs/say.wav`に保存 |
| `/voice 名前` | 声を切り替える（`/voice default`で標準の声） |
| Ctrl+C | 終了 |

1回だけ話させる、声を指定する場合:

```powershell
uv run --project "<presenter>/tools" presenter-tts say こんにちは、テストです。
uv run --project "<presenter>/tools" presenter-tts say --voice myvoice
```

長い文は「、」で区切って少しずつ生成する（区切りの間に短い間が入る）。本文がなかなか届かないときは、その声の「えーと」などのフィラーが入ることがある。

## 3. 声を登録する（voice）

録音と、その録音で話している内容をそのまま書いた文字起こしから、声を作る。

```powershell
uv run --project "<presenter>/tools" presenter-tts voice add myvoice 録音.wav --text "録音で話している内容"
uv run --project "<presenter>/tools" presenter-tts voice list
uv run --project "<presenter>/tools" presenter-tts voice remove myvoice
```

- 使ってよい声（自分の声、または本人の許可を得た声）だけを登録する。
- 録音は5〜30秒、ピアノやBGM、ほかの人の声が入っていない区間がよい。長い録音は、話している区間だけを切り出してから使う。
- 小さい録音は自動で音量を上げてから登録する（`--no-boost`で無効）。
- 作成後、確認用の文を`outputs/voice-<名前>.wav`に保存して再生する（`--no-preview`で再生しない）。
- 既定ではCPUで作る（約30秒〜1分）。サーバーを止めているときは`--gpu`で速くできる。
- `voice list`の`*`は、`--voice`を付けないときに使う声（設定の`voice = "名前"`）。
- 声は`<home>/voices/`に入り、どのプロジェクトからも名前で使える（元の録音は`<home>/voices/sources/`に置くとよい）。
  `<home>/cache/`とは分けてあるので、容量を空けるために`cache/`を消しても声は残る。

| オプション | 意味 |
|---|---|
| `--text`、`-t` | 文字起こし |
| `--text-file` | 文字起こしをファイルから読む |
| `--force` | 同じ名前の声を作り直す |
| `--sample` | 確認用に話させる文 |

## 4. 発表原稿を読み上げる（speak、plan）

SSMLで書いた原稿をWAVにする。

```powershell
uv run --project "<presenter>/tools" presenter-tts plan "<presenter>/tools/examples/presentation.ssml"
uv run --project "<presenter>/tools" presenter-tts speak "<presenter>/tools/examples/presentation.ssml" --force
uv run --project "<presenter>/tools" presenter-tts speak "<presenter>/tools/examples/presentation.ssml" --voice myvoice --play --force
```

- `plan`はモデルを使わず、区切り・間・マーカーをJSONで表示する。原稿の確認用。
- `speak`は`outputs/presentation.wav`に保存する（`--output`で変更）。同名のJSONに区間ごとの時間も保存する。
- `--play`は生成しながら再生する。生成が再生より遅いため、途中で途切れることがある。
- 既存の出力は`--force`を付けない限り上書きしない。

## 5. OpenAI互換のAPI（api）

OpenAIの音声合成API（`POST /v1/audio/speech`）と同じ形で使えるサーバーを立てる。
OpenAIのSDKや、OpenAI互換のTTSに対応したアプリから、接続先を変えるだけで使える。

```powershell
uv run --project "<presenter>/tools" presenter-tts api --voice myvoice
```

起動すると、接続先とAPIキーを表示する。Ctrl+Cで止まる。TTSサーバーが止まっていれば自動で起動する。

```
OpenAI-compatible speech API: http://localhost:8000/v1 (Ctrl+C stops)
Listening on 0.0.0.0: other devices use this PC's IP address.
API key: ...
```

- 既定では`0.0.0.0:8000`で待ち受け、同じネットワークのほかの機器（スマホ、別のPC）からも使える。ほかの機器からは`http://<このPCのIPアドレス>:8000/v1`へ接続する。初回にWindowsのファイアウォールの確認が出たら、プライベートネットワークを許可する。
- ネットワークに公開すると、だれでもクローンした声で話させられてしまうため、APIキーが必須。キーは初回に作って`<home>/api-key.txt`に保存し、以降も同じものを使う。自分で決める場合は`--api-key`か環境変数`PRESENTER_TTS_API_KEY`。
- このPCだけで使うなら`--host 127.0.0.1`。この場合はキーが不要になる。
- `--port`で待ち受けるポートを変えられる。

リクエストの項目:

| 項目 | 内容 |
|---|---|
| `input` | 読み上げる文章（4096文字まで） |
| `voice` | 登録した声の名前（`myvoice`など）。`alloy`などOpenAIの声の名前や省略時は`--voice`の声 |
| `response_format` | `mp3`（既定）、`opus`、`aac`、`flac`、`wav`、`pcm`（24 kHz・16ビット・モノラル） |
| `speed` | 話す速さ（0.25〜4.0、既定1.0） |
| `model` | 何を指定してもよい（`tts-1`など） |

音声は生成しながら返す（ストリーミング）。s2.cppは一度に1件しか処理できないため、同時に来たリクエストは順番に処理する。

PowerShellから試す例（`<APIキー>`は起動時に表示されたもの）:

```powershell
$body = @{ model = "tts-1"; input = "こんにちは。"; voice = "myvoice" } | ConvertTo-Json
Invoke-WebRequest http://localhost:8000/v1/audio/speech -Method Post -Headers @{ Authorization = "Bearer <APIキー>" } -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile hello.mp3
```

OpenAIのPython SDKから使う例:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="<APIキー>")
with client.audio.speech.with_streaming_response.create(
        model="tts-1", voice="myvoice", input="こんにちは。") as response:
    response.stream_to_file("hello.mp3")
```

## 6. 調子が悪いとき（doctor）

```powershell
uv run --project "<presenter>/tools" presenter-tts doctor
```

GPU、FFmpeg/FFplay、ネットワーク、サーバーのポートをまとめて確認する。

| 症状 | 確認すること |
|---|---|
| `Cannot reach http://127.0.0.1:3030` | `server status`。止まっていれば`server start` |
| 再生がプツプツする | 設定の`stream_start_buffer_ms_per_char`を上げる |
| クローンした声が小さい | 録音の音量。`voice add --force`で作り直す |
| 声が似ていない | 録音を長くする（10秒以上）、文字起こしが録音と合っているか確認 |
| サーバーが起動しない | `<home>/cache/s2-server.log`の末尾。GPUメモリ不足なら`[server] gpu_layers`を下げる |
| APIが`401`を返す | `Authorization: Bearer <APIキー>`。キーは`<home>/api-key.txt` |
| ほかの機器からAPIにつながらない | Windowsのファイアウォールでpythonを許可しているか、IPアドレスが合っているか |

## 7. 設定（<home>/config.toml）

| 場所 | 項目 | 意味 |
|---|---|---|
| 先頭 | `voice` | 普段使う声の名前（`voice list`の名前）か`.s2voice`のパス |
| `[generation]` | `stream_start_buffer_ms_per_char` | 話し始める前に、1文字あたりためる時間（途切れ対策） |
| `[server]` | `auto_start` | `say`/`speak`でサーバーを自動起動するか（既定true） |
| `[server]` | `gpu_layers` | GPUに載せる層の数（既定36＝全部） |
| `[server]` | `codec_gpu` | 音声コーデックもGPUで動かすか（既定true） |
| `[say]` | `filler` | フィラーを使うか |
| `[say]` | `filler_after_ms` | 本文がこの時間までに届かなければフィラーを流す |
| `[say]` | `filler_probability` | フィラーを流す確率 |
| `[say]` | `max_chars` | 長い文を区切る目安の文字数 |
| `[say]` | `split_first_clause` | 最初の文を「、」で分けて話し始めを早くするか |

すべてのオプションは`--help`で確認できる。

```powershell
uv run --project "<presenter>/tools" presenter-tts --help
uv run --project "<presenter>/tools" presenter-tts voice add --help
```
