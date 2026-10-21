# RRF原稿の短縮改稿と検査

これは比較試験とは別の改稿実走です。元の試行ファイルは上書きしていません。比較相手の出力は参照していません。

## 入力と変更

入力資料は `../../iteration-1/novice-rrf/brief.json` のS1とS2のみです。聞き手は検索技術を知らない同僚、目的は順位による検索結果の統合の理解、目標時間は60秒、ライブ発表です。

SDSを使用しました。全体像、処理の順番、留保、要点の順に話します。補助例と長い結びを削り、順位から値を計算すること、同じ文書の値を足して並べ直すこと、検索方式ごとのスコアを直接比較しないことを維持しました。未実験であることと、常に精度が改善するとは言えないことも本文に残しました。本文、演出、間は分離しています。

## 内容のレビュー

生成担当とは別のモデル担当が、rubric、入力資料、改稿原稿を照合しました。ただし、その担当は生成過程を含む会話を継承しているため、完全な独立評価とは扱いません。旧原稿、比較相手、機械検査結果を評価対象として読み込んでいません。この参考レビューは比較試験の勝敗には使用しません。

レビューではcritical issueは報告されませんでした。S1に対応する手順と直接比較しない条件、S2に対応する未実験と非保証が保持されていることを確認しました。対象を明確にするため、processの「それぞれの検索で」を「それぞれの検索結果で」に修正しました。この修正後にcheckとverifyを再実行しています。

参考レビューの予測評価は fidelity=5、audience_fit=4、structure=5、spoken_japanese=4、delivery_readiness=5、pacing=null でした。これらは人が聞いた理解度の測定値ではありません。audience_fitの指摘は上記の修正に反映しました。

## 最終版の機械検査

検査器は `skills/presentation-ja/scripts/presentation_check.py`、版は0.1.0です。元のbriefと、このディレクトリのscript.jsonを入力として、`check`と`verify`を順に実行しました。

- check: `mechanical_checks_completed`
- hard_errors: なし
- review_findings: なし。残存警告はありません。
- verify: `verified=true`
- 文字・数字の計数: 195
- 明示した間: 合計3秒
- 既定の仮速度: 240〜360文字/分
- 概算時間: 35.50〜51.75秒
- 目標時間: 60秒
- script SHA-256: `1ec80ee5ef98e37880764f571d48ee9a1cc5a3c59d8f4ca93ad7645e5d27e985`

概算上限は60秒以下です。既定速度による工学的な概算であり、実際の発話時間の保証ではありません。

## 未確認点

- audio: `not_assessed`
- human_listening: `not_assessed`
- comprehension_testing: `not_assessed`
- measured_audio_seconds: `null`
- 完全に独立した文脈での意味評価: `not_assessed`

機械検査のsemantic_fidelityとhuman_listeningは、仕様どおり`not_assessed`のままです。音声生成、録音、外部API、有料処理は実行していません。

## 成果物

- `script.json`: 本文、演出、根拠の対応
- `checked/manuscript.txt`: 読み上げ本文のみ
- `checked/speaker-notes.md`: 本文と演出指示
- `checked/check.json`: 機械検査結果
- `checked/receipt.json`: 入出力のハッシュ
