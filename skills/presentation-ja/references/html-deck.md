# html-deckとの共同作業

html-deckは画面に見せる主張と図を作り、presentation-jaはその画面を見せながら何を話すかを作る。
スライドの文字数予算に読み上げ原稿を含めない。原稿をHTML内へ埋め込まず、デッキの `narration/` に分けて置く。
連携対象は `deck.md`, `theme.css`, `index.html`, `slides/*.html` を持つ編集用デッキ。
バンドル後の一枚HTMLやPDFからの自動復元は対象外である。

## 作業の受け渡し

新規デッキはhtml-deckの手順を進める。原稿の作成を希望していることが分かっていれば、`deck.md` の `## skills` に `発表原稿: presentation-ja` を記録する。
既存デッキでは `goal`, `audience`, `situation`, `takeaway`, `storyboard`, `accepted` を読む。
目的や時間が不明な場合だけ確認し、決まっていることを再インタビューしない。
`deck.md` に原稿へのリンクを置くなら、マップを作る前に書く。
例えば `narration/checked/speaker-notes.md` を参照する `## narration` 節を追加できる。

スライドを作る担当には、各枚の最上位の `.slide` に意味が安定したIDを付けてもらう。

```html
<article class="slide" data-slide-id="search-overview">
```

IDは英数字、`-`, `_` で作り、先頭は英数字にする。番号・ファイル名・表示順とは別の値である。
同じページを改稿・改名・並べ替えしてもIDを保持する。分割・統合など、役割を変える場合は新しいIDにする。
既存のhtml-deckへ自動で属性を追加する必要はない。IDがなければマップ側で生成し、以後は同じファイルか、完全に同じ内容のファイルとして引き継ぐ。
改名と内容変更が同時で推定できない場合は、勝手に同じIDへ割り当てない。

## マップを作る

html-deckの表示検査・修正を終え、ビューアのスライド一覧を同期してから実行する。
html-deckのコマンドは、そのインストール先の `tools` プロジェクトから呼ぶ。

```sh
uv run --project "<html-deck-skill>/tools" html-deck-check "<deck>"
python "<presentation-ja-skill>/scripts/html_deck_bridge.py" sync --deck "<deck>"
```

`<deck>/presentation-map.json` ができる。各スライドについて、表示順、安定ID、HTMLファイル、見出し、構成表の主張、静的な本文、HTMLのSHA-256を持つ。
`index.html` のスライド一覧がファイルの並びやタイトルと異なる場合は停止する。ビューアはこのツールでは書き換えない。
構成表のIDは、明示ID、ファイルのstem、先頭の番号（例: `01-overview.html` の `01`）から照合する。
各HTMLに一つの構成表の行が対応し、未対応の行やスライドがあれば停止する。

HTML、構成表、共有CSSに加え、CSSのimport、ローカルの図版やフォントなど、静的参照をたどれるファイルもハッシュに含める。
`.loop/` の報告や、参照されていない原稿ファイルは含めない。外部参照やデッキ外のファイルは取得しない。
srcset、動的に読み込まれる資産、JavaScriptが生成するスライド、CSSの高度なエスケープ構文はサポート範囲外である。
静的な抜き出しは、CSS適用後の見え方や図の意味を測定しない。重要な図はHTMLやhtml-deckのスクリーンショットも確認する。

## 原稿とスライドを対応づける

`brief.json` は元資料を根拠として作る。スライドに書いてあるという理由だけで未検証の数字を出典扱いにしない。
スライド別の時間配分は安定IDをキーにした `slide_targets` で指定できる。指定する場合は全枚を埋め、合計を持ち時間以下にする。

```json
{
  "slide_targets": {"search-overview": 30, "search-limits": 45}
}
```

上記はbriefへの追加フィールドの例。聞き手・目的・sourcesなどの共通フィールドも必要である。
`script.json` の追加部分は次の形にする。

```json
{
  "deck_binding": {"map_sha256": "presentation-map.jsonのファイル全体のSHA-256"},
  "segments": [
    {
      "id": "explain-overview",
      "slide_id": "search-overview",
      "slide_sha256": "マップに記録した該当HTMLのSHA-256",
      "role": "body",
      "text": "ここで説明する仕組みは、順位を使って検索結果を組み合わせます。",
      "cue": "図の左から右へ示す。説明後に次のスライドへ切り替える。",
      "pause_after_seconds": 1,
      "evidence": []
    }
  ]
}
```

省略した共通フィールドは[形式の説明](contract.md)に従う。上記はフィールド説明であり、検査済みの完成例ではない。
一枚に複数のセグメントを割り当ててよい。同じスライドの話は連続させ、デッキの順で並べる。
現行の実装では全スライドに少なくとも一つの本文を対応させる。無言のデモや休憩だけのスライドを機械的にスキップする形式は未対応。
画面を読むだけの台本にならないよう、画面の主張を説明し、図の見る場所と次へ移る理由を言葉にする。

## 検査と成果物

```sh
python "<presentation-ja-skill>/scripts/presentation_check.py" check --deck "<deck>" --brief "<deck>/narration/brief.json" --script "<deck>/narration/script.json" --out "<deck>/narration/checked"
python "<presentation-ja-skill>/scripts/presentation_check.py" verify --deck "<deck>" --brief "<deck>/narration/brief.json" --script "<deck>/narration/script.json" --out "<deck>/narration/checked"
```

| ファイル | 用途 |
|---|---|
| `speaker-notes.md` | 表示番号・安定ID・HTMLへの対応、話す本文、演出、時間配分 |
| `by-slide/<id>.txt` | そのスライドで読む本文だけ。音声生成にも渡せる |
| `manuscript.txt` | デッキ全体の通し原稿 |
| `slide-timing.json` | スライドごとの時間概算と、開始時刻の概算範囲 |
| `check.json`, `receipt.json` | 警告、未評価の範囲、検査済みファイルの照合 |

表示順や網羅性の一致は構造上の検査である。主張と話す内容の意味の一致は独立評価で確かめる。
図の位置を示す言葉、スライドを切り替えるタイミング、用語の初出が実際の画面と合うかも読む。
原稿検査を通ったことを、html-deckの見た目の検査に合格したことにはしない。

## スライドが変わったら

内容や依存ファイルが変わると、古いマップでのcheck/verifyは失敗する。
html-deckで必要な表示検査を行い、マップを再作成して、影響する原稿と前後のつなぎを確認する。
全体のマップハッシュに加えてセグメントごとのHTMLハッシュも照合するため、全体のハッシュだけを更新しても古い原稿は通らない。
ただしハッシュは意味の確認を代行しない。変更箇所を読まずにセグメントのハッシュまで更新しない。

並べ替えた場合は安定IDを保ってセグメントを並べ直し、導入とつなぎを確認する。
追加・削除・分割・統合で対応が変わった場合は、漏れや余分な原稿を解決する。
出力ファイルの構成が変わるときは `checked-v2` など新しい保存先を選び、古いby-slideファイルを混在させない。
presenterで音声を作るときは、`presenter-tts narrate "<deck>" --checked "<deck>/narration/checked-v2"`で、その検査結果のフォルダを渡す。
ユーザーに毎回確認を求める必要はなく、既存の目的と確定した判断を保てる範囲で修正を進める。
