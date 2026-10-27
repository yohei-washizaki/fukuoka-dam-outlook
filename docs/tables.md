# Normalized tables

`PYTHONPATH=src python -m dam_outlook.normalize` が `data/raw/` から毎回作り直す派生データ（`data/normalized/*.csv`）。公表値だけを入れ、モデルの出力は入れない。解釈を含む列はその旨を書く。

## dam_storage.csv

1行 = 日付 × ダム（9ダム＋`total`）。日付は各日9時現在の観測日。

| Column | Meaning |
|---|---|
| `date` | 観測日（9時現在） |
| `dam_id` / `dam_name` | `total`, `minamihata`, `gokayama`, `sefuri`, `magarifuchi`, `egawa`, `kubara`, `nagatani`, `inono`, `zuibaiji` |
| `storage_pct` | 貯水率（%）。浄水日報では B/A×100。`bodik_hourly` だけの日は貯水量をその日の利水容量で割って求めた値 |
| `storage_m3` | 貯水量・現在利水量（m³） |
| `capacity_m3` | 利水容量 A（m³）。日報の値。`bodik_hourly` だけの日は、日報のダム別の値と南畑ダムの洪水期（6/1〜10/20）の値（`normalize.CAPACITY_M3`）。**日によって変わる**（下記） |
| `change_m3` | 増減水量（本日9時と前日9時の利水量の差、m³/日）。日報のみ |
| `precip_mm` | ダム地点の降水量（前日9時〜本日9時と推定、mm）。日報の `-` を 0 と読んだ値（解釈） |
| `precip_raw` | 日報の降水量欄の表記そのまま |
| `sources` | `nippou`（浄水日報）、`city_text`（市テキスト版）、`bodik_hourly`（BODIK の毎正時 CSV の9時の値）。値が違えばこの順で前のものを採り、警告を出す（`bodik_hourly` との差が2千m³以内なら千m³の丸めとみなして警告しない） |

利水容量は一定ではない。2026-10-21 に南畑ダムの利水容量が 3,650,000 → 5,120,000 m³、合計が 76,877,000 → 78,347,000 m³ に変わり、貯水量が増えたのに貯水率は 73.23% → 52.77% に下がった（洪水期の制限水位の解除とみられるが未確認）。予測・検証は貯水量（m³）で行い、貯水率はその日の容量で換算する。

## dam_storage_excl.csv

9ダム合計の渇水対策容量分を除く値（公表値）。1行 = 日付（9時現在）。

| Column | Meaning |
|---|---|
| `storage_pct` / `storage_m3` | 除く貯水率（%）・貯水量（m³、千m³ 単位） |
| `capacity_m3` | 除く利水容量。その日の合計の利水容量 − 渇水対策容量 23,276千m³（`RESERVE_CAPACITY_M3`）で求めた値（印字されていない） |
| `incl_storage_pct` / `incl_storage_m3` | 同じページに載った含む値。`city_status` のみ |
| `sources` | `city_status`（きょうのダム状況 002.html、2026-10-27〜の平日）、`report`（議会報告資料の本文に印字された値）。両方あれば `city_status` を採り、値が違えば警告を出す |

2026-02-17 の報告資料は 15,008千m³・27.0% と印字しているが、55,071千m³ で割ると 27.25%。印字のまま入れている。

公表されない日（土日、2026-10-26 以前）の除く値は `dam_outlook.reserve` が推定する（表には入れない）。渇水対策容量分（含む − 除く）がわかる日を基準点として、その間は線形補間、最後の基準点より後は据え置き、その日の含む合計から引く。基準点は、公表値の日（002.html はそのページの含む値、報告資料はその日の含む合計）と、報告資料のグラフで両方の線がある日（2025-04〜、`dam_storage_chart.csv` の中央値の差）。確認: 9/29（報告資料）と 10/27（002.html）の間を補間した 10/26 の推定 16,360,679 m³ は、市の公表値 16,361千m³ と一致した。

## distribution.csv

浄水日報の配水量（前日9時〜本日9時、m³/日）。1行 = 日付 × 浄水場（`takamiya`, `fufuishi`, `otogana`, `tatara`, `zuibaiji`, `shimobaru`, `total`）。

## precip_daily.csv

アメダス（福岡 82182、前原 82171、太宰府 82191）の日降水量（mm）。

- `precip_mm`: 暦日（00:10〜24:00、気象庁の日界）。
- `precip_9to9_mm`: 前日9時〜当日9時。ダムの9時現在の値と揃えるための列。

区切り時刻の `precipitation24h` を使い、無ければ毎正時の `precipitation1h` 24個の和。どちらも揃わなければ空欄。地点データには10分値の欠けがある（2026-10-17、10-20 など）。品質フラグが 0 以外の値は欠測扱い。

## jma_daily_precip.csv

気象庁「過去の気象データ」の日ごとの降水量（`jma_etrn_daily`）。1行が1地点・1日。

| Column | Meaning |
|---|---|
| `date` | 日付（暦日、気象庁の日界） |
| `station_code` / `station_name` | アメダス地点番号（82182 福岡、82171 前原、82191 太宰府） |
| `precip_mm` | 降水量の合計（mm）。`--`（現象なし）は 0、準正常値 `)` はそのまま使い、資料不足 `]`・欠測 `×` などは空 |
| `precip_raw` | 表の表記そのまま |

## jma_p1m.csv

1か月予報（九州北部地方 010900）の3階級確率（%）。`period` は `P1M`（期間全体）、`P7D`（1週目・2週目）、`P14D`（3〜4週目）。降水量の確率は期間全体だけ。

## jma_weekly_pop.csv / jma_weekly_precip_normal.csv

週間予報（福岡県 400000）の日別降水確率・信頼度と、福岡の7日間降水量の平年並の範囲（`min_mm`〜`max_mm`）。発表ごとに1組。

## dam_storage_chart.csv

`dam_outlook.digitize` が、市議会への水道局の報告資料のグラフから読み取った9ダム合計の貯水量。**推定値**（公表値ではない）。1行 = 日付 × 系列 × 報告資料。同じ日を複数の資料が描いていれば、資料ごとに1行ずつある。

| Column | Meaning |
|---|---|
| `date` | 日付。グラフの横軸は年度の365日分で、うるう年度（R5 など）の 2/29 以降は日付が決まらないので出さない |
| `series` | `incl_reserve`（渇水対策容量分を含む、実線）、`excl_reserve`（除く、破線） |
| `storage_m3` | 読み取った貯水量（m³、千m³ 単位に丸め） |
| `capacity_m3` | グラフの上に書かれた季節別の利水容量（`incl_reserve` のみ）。資料作成時点のもので、過去の年度も同じと仮定している |
| `fiscal_year` | 線のラベル（`R7` など） |
| `report_url` / `raw_path` / `page` | 読み取った資料 |
| `method` | 読み取り方法の版（`chart-vector v1`） |

確認した精度: 資料に印字された値との差は 2万m³ 程度まで（例: 2026-09-29 含む 33,914千m³ → 読み取り 33,893千m³）。資料どうしの同じ日の差は中央値 2.9万m³、最大 5.6万m³。

## verification/hindcast.csv

`python -m dam_outlook.hindcast` が作り直す、過去の基準日からの後ろ向きの答え合わせ（モデル評価で、発行した予測ではない）。条件は [models.md](models.md)。

| Column | Meaning |
|---|---|
| `model` / `model_version` | モデル |
| `horizon_days` | 予測日数（7・14・28） |
| `n` | 基準日の数 |
| `mae_m3` / `bias_m3` | 中央値（基準予測はその値）の平均絶対誤差・平均誤差（予測−実績、m³） |
| `crps_approx_m3` | 10・50・90パーセンタイルの分位点損失の平均の2倍（CRPS の近似。値が1つの予測では絶対誤差と同じ） |
| `p10_p90_coverage` | 実績が10〜90%の範囲に入った割合（0.8 が目安） |
| `first_base` / `last_base` / `generated_at` | 基準日の範囲と作成時刻 |
