# fukuoka-dam-outlook

福岡市関連9ダム合計の貯水量（渇水対策容量分を除く）について、28日先までの確率つきの見通しを毎日出し、実績と比べて検証する個人の非公式プロジェクトです。福岡市・気象庁とは関係ありません。サイト: https://dam-outlook.pages.dev/

このリポジトリは、非公開の開発用リポジトリから自動で書き出した公開用の写しです。データの取得と予測のたびに更新されます。ここへの直接の変更は次の同期で上書きされます。

## 含まれるもの

- `src/dam_outlook/`: 取得・正規化・予測・検証・サイト生成のコード
- `tests/`: テスト
- `docs/`: データ源・表の定義（[docs/tables.md](docs/tables.md)）・モデル（[docs/models.md](docs/models.md)）・判断の記録
- `data/normalized/`: 取得した元データから作った日次の表
- `data/forecasts/`: 発行した予測（1件1ファイル。発行後に書き換えない）
- `data/verification/`: 検証の結果
- `data/curated/events.csv`: 筑後川の取水制限率の変更（出典つき）
- `data/curated/storage_chart_sources.csv`: 過去の貯水量を読み取った報告資料の一覧

## 含まれないもの

- `data/raw/`: 取得した元のページ・PDF の写し。福岡市の掲載情報は無断の複製・転用が認められていないためです。テストのうち市のページの写しを使うものは、ここではスキップされます。
- 福岡市の個別ページの URL。個別ページへのリンクには市の担当課への問い合わせが必要なため、`https://www.city.fukuoka.lg.jp/...` に置き換えています。このため、市のページを取得する部分（`dam_outlook.fetch` の一部）はここでは動きません。
- 出来事の照合メモ、GitHub Actions の設定などの開発用のファイル。

## 検証のしかた

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

- **予測を計算し直す**: `PYTHONPATH=src .venv/bin/python -m dam_outlook.forecast --forecast-dir /tmp/forecasts`。予測を発行した直後の同期コミットの `data/normalized/` から実行すると、保存された予測と同じ入力（`inputs_sha256`）と同じ結果になります。乱数の種は入力から決まります。
- **検証をし直す**: `PYTHONPATH=src .venv/bin/python -m dam_outlook.verify --out-dir /tmp/verification`。`data/verification/scores.csv`（予測1件・対象日1日ごと）と `summary.csv` を作ります。
- **過去にさかのぼった検証**: `PYTHONPATH=src .venv/bin/python -m dam_outlook.hindcast --out /tmp/hindcast.csv`（数分）。2023-08 以降の毎週の基準日について、その日までのデータだけで予測し直します。
- **予測が後から書き換えられていないこと**: 各予測ファイルの `issued_at` と、このリポジトリでそのファイルが最初に現れたコミットを比べられます。このリポジトリの履歴は公開を始めた時点からです。

## 公表値と推定値

- `data/normalized/dam_storage.csv` は福岡市水道局が公表した値です（「きょうのダム状況（テキスト版）」「浄水日報」と、BODIK の「福岡市関連９ダム貯水量」の毎日9時の値）。
- `data/normalized/dam_storage_chart.csv` は、市議会への報告資料「水事情について」のグラフから読み取った推定値で、公表値ではありません（誤差は2万m³程度）。
- `data/forecasts/` と `data/verification/` は、このプロジェクト独自の計算です。

## 出典

- 福岡市水道局「きょうのダム状況（テキスト版）」「浄水日報」、市議会への報告資料「水事情について」
- 福岡市「福岡市関連９ダム貯水量」（BODIK、CC BY）を加工して作成
- 気象庁ホームページ（https://www.jma.go.jp/）の1か月予報・週間予報・アメダス観測値・過去の気象データ。これらを加工して作成しており、編集・加工の責任はこのプロジェクトにあります。
- 福岡地区水道企業団などの発表（`data/curated/events.csv` の `source` 列）
