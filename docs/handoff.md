# Handoff (2026-10-25)

atelier の Claude Code セッション（knowledge-base での壁打ち）から、forge での実装セッションへの引き継ぎ。

## Context

- 福岡県・福岡市では2026年に少雨が続き、筑後川の取水制限が年2回あった（`data/curated/events.csv`）。
- 公式の貯水率表示は見づらい。市のテキスト版は現在値だけで過去値がなく、県は PDF 中心。
- 壁打ちの結果、「見やすい表示」ではなく「この先どうなるか」を統計的に予測し、その答え合わせで自己改善する仕組みにすると決めた。決定事項は [plan.md](plan.md) の Decisions を正とする。

## Done

- リポジトリ作成（private）、README / AGENTS.md / docs / 出来事の初期データ / テスト雛形。
- データ源の取得可否を 2026-10-25 に確認（[data-sources.md](data-sources.md)）。
- 2026-10-25（forge）: plan.md の Today 1〜7 を実装し、main に push。GitHub Actions `fetch.yml` が毎日 18:07 JST（2026-10-26 までは 14:45 / 0:20）に 取得 → 正規化 → 予測 → 検証 → サイト生成 を行い、データをコミットする。サイトは成果物 `site` としてアップロードするだけで、公開はしていない。
  - 取得: `dam_outlook.fetch`（`data/raw/`、上書きなし、`_log/` に全試行）
  - 正規化: `dam_outlook.normalize`（`data/normalized/`、[tables.md](tables.md)）。浄水日報 PDF は pypdf で読む
  - 予測: `dam_outlook.forecast` / `models`（`data/forecasts/`、[models.md](models.md)）。`baseline_norain` v1 と `scenario_jma_tercile` v1（2026-10-25 に v0 から更新）
  - 検証: `dam_outlook.verify`（`data/verification/`）
  - サイト: `dam_outlook.site`（`site/index.html`）
- Dependabot（GitHub Actions・pip）と、alerts / security updates を有効化。

## Not done

- `events.csv` は取水制限率が変わった日だけを載せる（2026-10-25 に範囲を絞り、組織の設置・解散などを外した）。サイトではグラフの背景に制限率を重ねる。
- 公開: Cloudflare Pages の Git 連携に決定（[publishing.md](publishing.md)）。
- ダム諸量データベースには forge からも接続タイムアウト。過去の貯水量は議会報告資料のグラフから推定している（公表値ではない）。
- 答え合わせは、最初の予測（基準日 10/23）の対象日の公表値が出る 10/26 以降に始まる。
- 2026-10-25: 報告資料のグラフから読み取った過去の貯水量（2022-04〜）と、気象庁「過去の気象データ」の日降水量（2006〜）で、v1 を作った。雨への反応を履歴から推定し、雨のシナリオは過去の同じ季節の実際の雨。後ろ向きの答え合わせ（`dam_outlook.hindcast`）で選んだ（[models.md](models.md)）。取水制限・節水の効果は、直近の減り方に表れる分だけ入っている。
- 過去の貯水量は 2026-09-30〜10-18 が空いている。次の議会報告資料のグラフで埋まる見込み。
- サイトはヘッドレスブラウザで表示を確認できていない（forge に Chromium の依存ライブラリがない）。

## Knowledge base

壁打ちの経緯は knowledge-base の `cases/2026-fukuoka-dam-outlook.md` に記録した。

実装セッションで得た知見のうち、knowledge-base に残す価値があるものは、そのセッションから `knowledge-base-local` スキルで knowledge-base に記録する。このリポジトリには知識ベースのノートを複製しない（AGENTS.md）。例:

- データ源の仕様・挙動（気象庁 bosai JSON の構造変更、浄水日報の公開範囲、ダム諸量データベースへの接続可否など）。
- forge 上の環境・GitHub Actions・Pages で詰まった点と解決方法。
- 予測・検証の設計で下した判断とその理由。

## Next (priority order)

1. 毎日の答え合わせで、v1 が後ろ向きの答え合わせどおりに当たるか（10〜90%の範囲に実績の約8割が入るか）を見る。外れ方に応じて版を上げ、古い予測は残す。
2. 予測モデルを継続的に改善する仕組みを作る（2026-10-25 に次の課題として決定）。毎日の答え合わせと後ろ向きの答え合わせ（`dam_outlook.hindcast`）をどうつなぎ、いつ・何を基準に版を上げるかを決める。
3. 新しい議会報告資料「水事情について」が出たら `data/curated/storage_chart_sources.csv` に足す（2026-09-30〜10-18 の空きが埋まる）。
