# ローカルのプレゼン音声生成

Python（uvで管理）でSSMLや入力文を解析し、量子化したFish Audio S2 Proを動かすs2.cppのHTTPサーバーへ渡す。
話速・音量・ピッチはFFmpegで処理し、受信したPCMからWAVとマーカー時刻のJSONを作る。
`--play`を付けるとFFplayへ受信中のPCMを送り、全文の音声完成を待たずに再生する。
Python側にPyTorchやモデルを読み込まないため、TTSサーバーと同じGPUメモリを二重に消費しない。

## 現在の検証状況

2026-10-21時点、**GeForce RTX 3080（VRAM 10 GB）・CUDA 12.9・Q4_K_M**で確認した。

- s2.cppをCUDA 12.9（SM86）でビルドし、全36層とコーデックをGPUに載せて動作。サーバーのVRAM使用は約4 GB。
- 生成速度は1フレーム（約46 ms分の音声）あたり約49 msで、生成だけのRTFは約1.07。ストリーミング用の再デコードを含めるとRTFは約1.3〜1.4で、実時間より遅い。
  CUDAグラフの有効化やCPUスレッド数（4・8・16）では変わらなかった。s2.cppの構造（Fast-ARを毎回組み立て直す）による上限と考えている。
- 実時間より遅いため、生成しながら再生すると途中で途切れる。`[generation] stream_start_buffer_ms_per_char`で文ごとに先行して溜め、`say`は文を区切って1文ずつ生成する。
- `say`で最初の音が出るまでは約0.9〜1.2秒（最初の区切りを短くした場合）。
- 声のクローン（`voice add`）、OpenAI互換API（`api`）、サーバーの裏起動（`server`）も実機で確認した。
- テストはモデルなしで実行できる（s2.cpp・FFmpeg・HTTPはスタブやループバックで代替）。

## まずSSMLを検証する

環境診断と実機の速度計測をまとめる場合は、[検証コマンド](tts-cuda-wsl.md#検証結果をまとめて保存する)を参照。

リポジトリ直下で実行する。uvのプロジェクトなので、最初に`uv sync`で`.venv`へ依存関係（Python 3.11、Typer）を入れる。
以降は`uv run`が`.venv`を使うので、仮想環境を有効にする必要はない。

```powershell
uv sync
uv run presenter-tts plan examples/presentation.ssml
uv run presenter-tts doctor
uv run python -m unittest discover -s tests -v
```

`plan`はモデルを使わず、文章・間・演出パラメータ・マーカーをJSONで表示する。
`doctor`はGPU空き容量、FFmpeg/FFplay、DNS、設定されたTTSポートを確認する。
ポート疎通だけで音声合成の動作を保証するものではない。

## S2 Proのセットアップ

CUDA更新・WSL側の診断と設定方針は[CUDAとWSLの確認](tts-cuda-wsl.md)を参照。

必要なもの:

- Visual Studio 2022の「C++によるデスクトップ開発」とCMakeツール
- CUDA Toolkit 12.4以上。`nvcc --version`で確認する。`nvidia-smi`に表示されるCUDAの値はドライバー側の対応値。
- Git、uv（Python 3.11は`uv sync`が用意する）、FFmpegとFFplay
- モデル約3.6GB、トークナイザー約12MBに加え、ソースとビルド用のディスク空き容量
- GitHubとHugging Faceへの通信

```powershell
.\tools\setup-s2.ps1 -CheckOnly
.\tools\setup-s2.ps1
```

セットアップは`.runtime/`内にs2.cppとggmlを取得し、RTX 3080のSM86向けにビルドする。
GGUFは`rodrigomt/s2-pro-gguf`の`Q4_K_M`を取得する。
モデルの公開LFS SHA-256を照合し、ダウンロード途中のファイルは`.part`として扱う。
利用したモデルのリビジョンとハッシュを`.runtime/models/provenance.json`、ソースのコミットを
`.runtime/s2-source-revision.txt`に記録する。既存のビルド先に対して自動で最新版へ更新しない。

モデルは`.runtime/models/`に置き、同じディレクトリに対応する`tokenizer.json`を置く。
別形式のNF4・GPTQ・FP8チェックポイントをGGUFとして指定してはいけない。

TTSサーバーは`uv run presenter-tts server start`で裏で起動する（[使い方](usage.md#1-ttsサーバー)）。
`say`・`speak`・`api`は、止まっていれば自動で起動する。出力は`.runtime/s2-server.log`に残る。

既定はGPUに全36層、コーデックもGPU。VRAMが足りずに起動できないときは、
`presenter.local.toml`の`[server]`で`gpu_layers`を減らす（例: 18）か、`codec_gpu = false`にする。
上流READMEではQ4_K_Mの全GPU実行で約7.2GBを見込むが、実測のサーバー分は約4 GBだった。
`--max-tokens`を小さくしても、s2.cppが事前確保するKVキャッシュ全体が小さくなるわけではない。

```powershell
Copy-Item presenter.example.toml presenter.local.toml
uv run presenter-tts speak examples/presentation.ssml --output outputs/presentation.wav --play
```

WAVと同名のJSONに、各発話の長さ・マーカーのフレーム位置・最初のPCM到着時間・RTFを保存する。
`--play`ありの測定には再生パイプの待ち時間も含まれる。純粋な生成速度は`--play`なしでも測る。
既存の出力は`--force`を付けない限り上書きしない。TTS失敗時は未完成のWAVを公開しない。

声を区間ごとに揃えるには、`voice add`で登録した声の名前を設定する（次の節）。
参照音声は利用許諾のあるものを使う。

```toml
backend = "s2cpp"
url = "http://127.0.0.1:3030"
voice = "myvoice"
```

未設定でもリクエストできるが、各区間で声質が変わる可能性がある。

### 声を登録する

録音と、その録音で話している内容をそのまま書いた文字起こしから、声のプロファイルを作る。

```powershell
uv run presenter-tts voice add myvoice 録音.wav --text "録音で話している内容"
```

- 録音はFFmpegが読める形式なら何でもよい。44.1 kHzモノラルに変換し、ピークが-3 dBになるよう音量を上げてから登録する（小さい録音からは小さい声ができるため。`--no-boost`で無効）。
- 5〜30秒の録音を推奨する。範囲外なら警告を出す。
- TTSサーバーとGPUを取り合わないよう、既定ではCPUで作成する（約30秒〜1分）。サーバーを止めているときは`--gpu`で速くできる。
- 作成後、確認用の文を`outputs/voice-<名前>.wav`に保存して再生する（`--no-preview`で再生しない）。
- 同じ名前は`--force`を付けない限り上書きしない。置き換えた場合、その声のフィラーは作り直される。

登録した声はプロジェクト直下の`voices/`に入り、名前で使える（gitには入らない）。
`.runtime/`（エンジン・モデル・キャッシュ）とは分けてあるので、容量を空けるために`.runtime/`を消しても声は残る。

```powershell
uv run presenter-tts voice list
uv run presenter-tts say --voice myvoice
uv run presenter-tts speak examples/presentation.ssml --voice myvoice --force
uv run presenter-tts voice remove myvoice
```

`voice list`の`*`は`presenter.local.toml`の`voice`で選ばれている声。`--voice default`はクローンしない標準の声。
対話モードの`say`では、`/voice 名前`で途中から切り替えられる。

## SSMLの対応範囲

**W3C SSML 1.1の限定したサブセットを受け付ける。SSML全機能への適合や認証を意味しない。**
独自XMLタグやFish独自トークンを原稿に混ぜず、対応外のタグ・属性・値は合成前にエラーにする。
入力は最大128KB、深さ24、1発話300文字まで。長い文章は`<s>`や句点で区切る。
合成結果は1回30分まで。サーバーの生成上限まで到達した場合に全文を読めたかは、実音声の確認が必要。

| 要素・指定 | この実装での扱い |
| --- | --- |
| `speak` | W3Cの名前空間、`version="1.1"`、`xml:lang`を必須にする |
| `xml:lang` | `ja`、`ja-JP`、`en`、`en-US`。声や発音辞書の切り替えはしない |
| `s` / `p` | 発話の境界。追加の固定無音は挿入しない。必要な間は`break`で指定する |
| `break time` | `700ms`、`0.7s`など。0〜10秒の無音をサンプル数で挿入 |
| `break strength` | none=0、x-weak=80、weak=150、medium=300、strong=600、x-strong=1000msの追加無音に近似 |
| `prosody rate` | `50%`〜`200%`はデフォルト話速への倍率。入れ子でも掛け合わせない。ラベル値にも対応 |
| `prosody pitch` | `+2st`などの相対半音、またはラベル。合計±12stまで |
| `prosody volume` | 符号付きdB、ラベル、silent。入れ子のdBを相対適用。defaultでsilentを解除 |
| `sub alias` | 対象の表記を読み上げ文字列へ置換する |
| `emphasis` | moderate/strongを話速・音量に近似。声の演技としての強調は保証しない。警告を出す |
| `mark` | 一意なnameを指定。生成済みPCMの正確なフレーム位置と時刻をJSONに記録 |

`break`で挿入する無音の長さは正確だが、TTSが生成した語尾・語頭の無音を含む「実際に聞こえる間」の長さは一致しないことがある。
`strength="none"`は追加無音をなくす指定として扱い、モデルが自動生成した間を除去するものではない。
ピッチは波形処理で変えるため声質も変わりうる。自然な強調やアクセントの保持は実音声で評価する。
`mark`は再生時のイベントを直接発火しない。スライド側はJSONの音声時刻とプレーヤーの再生位置を同期して使う。

`voice`、`phoneme`、`say-as`、`audio`、`lexicon`、`prosody contour/range/duration`などは未対応。
DTD・エンティティ宣言・外部音声の取得も禁止する。
W3C文法の全体をXSDで検証しているわけではなく、対応サブセットについての検証である。

## VOICEVOXで利用する

別ファイル`presenter.local.toml`に、次の内容だけを保存する。
標準VOICEVOX ENGINEを起動してから同じ`speak`コマンドを実行する。

```toml
backend = "voicevox"
url = "http://127.0.0.1:50021"
speaker = 3
```

標準APIでは発話区間のWAV完成後に再生を始める。音声波形を逐次返すS2経路とは異なる。
AivisSpeechは互換APIの仕様差があるため、この経路の検証対象に含めていない。

## 低遅延化の方針と、問題が出たときの調査

現在は原稿のSSML全体を検証してから、発話区間ごとに合成する。
各区間のS2出力はPCMストリーミングで受けるが、LLMから未完のXMLを受け取る逐次入力は未実装。
全体の検証を先に行うので、後半の未対応タグが見つかる前に誤って発話することを防げる。
低遅延用の設定でも、入力全文が確定するまでのLLM待ち時間は短くならない。

速度・OOM・音切れ・韻律の問題は、まず実測値と再現する原稿を残す。
モデルと推論実装のリビジョン、量子化形式、GPU空き容量、参照音声長、最初のPCM到着時間、RTF、波形を記録する。
その原因に関係する最新の論文と公開実装を確認し、設定変更だけで使える対策と、実装変更・学習が必要な研究を分ける。
採用前後を同じ原稿・声・ハードウェアで比較し、速度だけでなく読み落とし・音切れ・声質も確認する。

2026-10-20時点で確認した候補:

| 資料 | 今回への判断 |
| --- | --- |
| [Fish Audio S2 Technical Report](https://arxiv.org/abs/2603.08823) | Dual-ARとストリーミングの基本。論文の高速GPU上の値をRTX 3080の保証値にしない |
| [Prosodic Boundary-Aware Streaming Generation（2026-03）](https://arxiv.org/abs/2603.06444) | 区間の境界と過去文脈を使った韻律改善。現在のs2.cppへの適用は未検証 |
| [RVQ Position Aware Speculative Decoding（2026-09-29）](https://arxiv.org/abs/2609.37007) | Qwen3-TTSのRVQ生成をApple端末で2〜2.2倍高速化した報告。S2 ProやRTX 3080で同じ効果が出るとは扱わない。追加の学習とデコーダー変更が必要 |
| [WUSH-KV（2026-09-29）](https://arxiv.org/abs/2609.38121) | KVキャッシュの低ビット化。s2.cppの設定で直ちに使える方式ではなく、S2の音声品質も未検証 |

CUDA不足やDNS・ソケット制限は環境上の問題として別に記録し、モデルの速度や品質の問題と混同しない。

## 参照した仕様・実装

- [W3C SSML 1.1](https://www.w3.org/TR/speech-synthesis11/)
- [s2.cppのAPI・メモリ使用量・制約](https://github.com/rodrigomatta/s2.cpp)
- [Q4 GGUF配布元](https://huggingface.co/rodrigomt/s2-pro-gguf)
- [SGLang-OmniのS2 Pro実装](https://sgl-project.github.io/sglang-omni/cookbook/fishaudio_s2_pro.html)
- [Fishの公式ライセンス](https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md)

Fish S2 Proのモデルとs2.cppは独自ライセンス。研究・非商用での利用と業務・商用利用の条件を区別する。
モデルを量子化しても利用条件は変わらない。
