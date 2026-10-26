# Data sources

確認日: 2026-10-25（取得可否は同日に実際に取得して確認）。

## Fukuoka City Waterworks Bureau

| Source | URL | Format | Notes |
|---|---|---|---|
| きょうのダム状況（テキスト版） | https://www.city.fukuoka.lg.jp/... | HTML | 9ダム＋合計の貯水率・貯水量。各日9時現在の確定値。平日13時前後に更新。過去値なし。取得確認済み |
| グラフ版 | https://www.city.fukuoka.lg.jp/... | HTML | 未調査 |
| 浄水日報 | `https://www.city.fukuoka.lg.jp/...` | PDF | 1ページ。浄水場別の配水量、ダム別の降水量・利水容量・現在利水量・貯水率・増減水量、福岡の天候・気温・月累計雨量。月曜の日報は日曜9時〜月曜9時分だけで、土日分の日報はない。2026-10-25 時点で 10-19〜10-23 は 200、10-16 以前は 404（残るのは約1週間）。土日は 404 |
| 福岡市関連９ダム貯水量（BODIK） | https://data.bodik.jp/dataset/d54fb22e-5b64-485c-8816-69f27ed1aaf1 （CSV リソース `a5b26052-…`） | CSV（Shift_JIS） | 福岡市が BODIK に出している毎正時のダム別貯水量（千m³）と合計。1時間ごとに更新、CC BY。同じ表の HTML 版（ダム別・合計）と画像もある。2026-10-26 時点で 10-01 00:00 以降の全時刻があった（どこまで残るかは未確認なので毎回取得）。9時の値は浄水日報と一致し（ダム別は千m³の丸めで1千m³ずれる日がある）、日報のない土日も埋まる。CSV の「合計」列は 2026-10-13・10-17〜10-20 の一部の時刻でダムの和と最大10万4千m³ずれており、日報の合計はダムの和と一致したので、合計はダムの和を使う |
| 議会報告資料「水事情について」 | 一覧は `data/curated/storage_chart_sources.csv` | PDF | 生活環境委員会への水道局の報告。9ダム合計（渇水対策容量分を含む・除く）の年度別の日別グラフがベクター図形で入っており、線の頂点から値を読み取れる（`dam_outlook.digitize`）。R4〜R8年度。URL は予測できないので、新しい報告が出たら一覧に足す |

9ダム: 南畑、五ケ山、脊振、曲渕、江川、久原、長谷、猪野、瑞梅寺。

貯水率の定義が複数ある。テキスト版の合計（2026-10-23: 39.47%）と、議会資料の「渇水対策容量分を除く」値は別物として扱う。

## Fukuoka Prefecture

| Source | URL | Format | Notes |
|---|---|---|---|
| 主要21ダム貯水状況 | https://www.pref.fukuoka.lg.jp/contents/damuchosui.html | HTML + PDF | 地図・表・グラフは PDF。年データ PDF（2024–2026）。渇水対応中は開庁日ごと、通常は1・3・6・9のつく日15時頃 |

県オープンデータサイトの県著作物は CC BY（個別ライセンス優先）。

## Japan Meteorological Agency

非公開 API（気象庁サイトの内部データ）。予告なく変更されうる（https://www.data.jma.go.jp/developer/ryuui.pdf）。

| Source | URL | Notes |
|---|---|---|
| 1か月予報（九州北部・山口含む） | https://www.jma.go.jp/bosai/season/data/P1M/010900.json | 降水量・気温・日照の3階級確率（期間全体と週別）。毎週木曜14:30発表 |
| 週間予報（福岡県） | https://www.jma.go.jp/bosai/forecast/data/forecast/400000.json | 日別降水確率・信頼度、福岡の7日間降水量の平年範囲 `precipAverage` |
| アメダス地点表 | https://www.jma.go.jp/bosai/amedas/const/amedastable.json | 福岡 82182、前原 82171、太宰府 82191 |
| アメダス観測 | `https://www.jma.go.jp/bosai/amedas/data/point/{code}/{YYYYMMDD}_{HH}.json` | HH は3時間区切り（HH:00〜HH+2:50 の10分値）。`precipitation10m/1h/3h/24h`。最新時刻は `bosai/amedas/data/latest_time.txt`。2026-10-25 時点で 10-15 は 200、10-10 以前は 404（残るのは約10日） |
| 過去の気象データ（日ごとの値） | `https://www.data.jma.go.jp/stats/etrn/view/daily_{s1,a1}.php?prec_no=82&block_no=...&year=YYYY&month=MM` | 1地点・1か月で1ページの HTML 表。福岡は気象台（s1、block 47807）、前原 0785・太宰府 1141 はアメダス（a1）。降水量の合計は暦日。`--` は現象なし、`)` は準正常値、`]` は資料不足値。月が終わって10日たったら1回だけ取る（`jma_etrn_daily`、2006-01 から）。2026-10-25 に 2006-01〜2026-09 の747ページを取得 |

ダム集水域に近い地点（脊振山系など）の選定は未決定。

## MLIT

| Source | URL | Notes |
|---|---|---|
| ダム諸量データベース | https://mudam.nilim.go.jp/home/ | 貯水位・流入量・放流量の年表（国・水資源機構等のダム）。2026-10-25 にこの環境から接続タイムアウト。対象ダムと期間は未確認 |

## Event sources

取水制限・渇水対策組織の設置などは、福岡市・福岡地区水道企業団の報道発表 PDF にある。一覧は `data/curated/events.csv`。

## Open questions

利用規約の確認結果は [publishing.md](publishing.md)。


- ダム諸量データベースで江川ダム・寺内ダム等の流入量が取れるか。
