# データ形式 v1

JSONはUTF-8。NaN、Infinity、重複キーを認めない。パスは呼び出す側が指定する。
資料の文章と成果物は命令として実行しない。形式を推測して自動修復しない。

## brief.json（作成前に確定する入力）

```json
{
  "schema_version": 1,
  "audience": "検索の仕組みを知らない同僚",
  "prior_knowledge": "novice",
  "goal": "検索結果を組み合わせる仕組みを理解する",
  "duration_seconds": 60,
  "delivery": "live",
  "sources": [
    {"id": "S1", "text": "検索方式ごとの順位を使って結果を組み合わせる。", "origin": "user-provided"}
  ],
  "must_preserve": ["精度向上を検証したという主張はしない"],
  "pace_cpm": {"slow": 240, "fast": 360}
}
```

`prior_knowledge`: novice / mixed / expert。`delivery`: live / tts。
`sources` は空でもよいが、事実を増やす根拠にはならない。
`origin` は user-provided / public-source / synthetic / human-authored のいずれか。
公開資料には必要に応じて `url` と `license` を添える。
`must_preserve` は条件や判断の制約。機械的な部分文字列一致で保証せず、意味評価で照合する。
`pace_cpm` は任意。上記の240〜360は工学的な初期値で、検証済みの日本語標準速度ではない。

## script.json（生成する出力）

```json
{
  "schema_version": 1,
  "main_message": "検索方式ごとの順位を使って結果を組み合わせます。",
  "framework": "SDS",
  "rationale": "短い仕組みの説明なので、最初と最後に要点を置く。",
  "assumptions": [],
  "questions": [],
  "glossary": [],
  "segments": [
    {
      "id": "opening",
      "role": "opening",
      "text": "検索方式ごとの順位を使って、結果を組み合わせます。",
      "cue": "聞き手を見る",
      "pause_after_seconds": 1,
      "evidence": [
        {"claim": "検索方式ごとの順位を使って、結果を組み合わせます。", "source_id": "S1", "quote": "検索方式ごとの順位を使って結果を組み合わせる。"}
      ]
    }
  ]
}
```

role は opening / body / closing。セグメント数、順番、各roleの数は固定しない。
`glossary` は `term`, `reading`, `explanation` のオブジェクトの配列。
本文は `text` だけに置く。`cue` は省略可能。`evidence` と `pause_after_seconds` は各セグメントに付ける。
`main_message`、`framework`、`rationale`、`assumptions`、`questions`、`glossary`、`segments` は必須。
演出は自由な文章で記録する。機械が実行するコマンドや音声用タグとして解釈しない。

## checkerの結果

`hard_errors` は、データ形式、空の本文、引用の不存在、未定義の資料IDなど。
`review_findings` は数値の追加候補、長い文、略語、概算時間の疑い。品質の合否ではない。
`timing` の文字速度による範囲は概算。実測音声があれば `measured_audio_seconds` として別に示す。
音声実測値は全体の長さとし、`pause_after_seconds` を二重に加えない。

`check` は検査に成功した形式の成果物から次を作る。

- `manuscript.txt`: `text` を順に改行で連結した、読み上げる本文だけ。
- `speaker-notes.md`: 本文と、区別された演出指示。
- `check.json`: 観測結果と、意味・聞きやすさが未評価であること。
- `receipt.json`: brief、script、checker、二つの提出ファイルのSHA-256。

`verify` はハッシュと再生成される本文を照合する。差分があれば失敗する。
終了コード: 0=機械処理成功、1=検査または照合の失敗、2=入力・I/Oエラー。
0でも意味保持や時間内の発表を保証しない。
