# Handoff (2026-10-26)

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
- サイトの表示確認: forge には Chromium の依存ライブラリがないが、Playwright の chromium-headless-shell に、足りない共有ライブラリ（at-spi2-core・libx11 系・mesa の libgbm・alsa-lib など）を Arch のパッケージから作業用ディレクトリに展開して `LD_LIBRARY_PATH` で渡せば撮れる（2026-10-26）。

## Knowledge base

壁打ちの経緯は knowledge-base の `cases/2026-fukuoka-dam-outlook.md` に記録した。

実装セッションで得た知見のうち、knowledge-base に残す価値があるものは、そのセッションから `knowledge-base-local` スキルで knowledge-base に記録する。このリポジトリには知識ベースのノートを複製しない（AGENTS.md）。例:

- データ源の仕様・挙動（気象庁 bosai JSON の構造変更、浄水日報の公開範囲、ダム諸量データベースへの接続可否など）。
- forge 上の環境・GitHub Actions・Pages で詰まった点と解決方法。
- 予測・検証の設計で下した判断とその理由。

## 2026-10-26

- 継続的な改善: v1 は予測のたびに全履歴から係数・水準・ゆらぎを推定し直すので、データがたまるほど自動で良くなる。候補モデルと昇格のルール（2段目）は、しばらく運用して問題が見えるまで作らない（本人の判断）。
- サイトの「計算方法」を手順ごとに詳しくし、表示中の予測の推定値と、後ろ向きの答え合わせの結果を出した（PR #18）。
- 公開用リポジトリ https://github.com/yohei-washizaki/fukuoka-dam-outlook（public、ライセンスなし）を作り、`public.yml` で同期する（[publishing.md](publishing.md)）。サイトのフッターからリンクした。
- 気象庁から、予報業務に該当しないと回答があり、`noindex` を外した。
- 取得は毎日 18:07 JST の1回にした（速報性は不要）。GitHub Actions の cron は 3〜4時間遅れることがある。外部から起動する案（Cloudflare Workers の Cron Triggers → workflow_dispatch）は PAT が要るので見送った。
- 市のテキスト版が平日何時に更新されるかは、まだ観測で確かめていない（`data-sources.md` の「13時前後」は引き継ぎ時の調査。10/23 は Last-Modified 13:16、10/26 は 13:37 の時点で未更新）。

## 2026-10-26 サイトのデザイン（PR #20、マージ済み）

- 一番上は一文の見出し:「4週間後（11/20）の貯水率は、ほぼ横ばいの40%前後になるでしょう。雨が降らなければ、32%まで下がります。」。中央値といまの差が ±2ポイント以内（2ちょうどを含む）なら「ほぼ横ばいの○%前後になるでしょう」、それより上下なら「○%前後まで上がる／下がるでしょう」（`site.FLAT_PT`）。文言は本人が指定した。
- 見出しの下は「いまの貯水率（公表値）」と「4週間後の予想（モデル出力）」の2つだけ。範囲（「80%の範囲」「10回のうち8回」）は上部に出さない（本人の判断。グラフと表には残す）。
- ページの言葉は「予想」にそろえた（「見通し」「予測」は使わない。「予報」は気象庁の「1か月予報」の名前だけ）。
- 筑後川の取水制限は重要度が低い（本人の判断）。タイルは外し、グラフの背景の色を薄くし、経過の表は推移の下の折りたたみにした。
- 日本語の見出しは文節ごと・文ごとに inline-block にして、語の途中で改行しない（`site.phrases`）。
- グラフは PC 用とスマホ用の2枚を描き、CSS で切り替える。
- デザインを比べた案（A〜E、調べた事例つき）と、サイトのプレビューは claude.ai の非公開 Artifact に置いた。プレビュー用の Cloudflare ブランチ（`preview/*`）への push は、誰でも見られるため権限確認で止まった。

## Next (priority order)

1. サイトの残り: ダム別の表がスマホで横にスクロールする。推移セクションの「公表値」バッジ（グラフの大半は報告資料のグラフからの推定値）を変えるかは本人が決める。
2. 毎日の答え合わせで、v1 が後ろ向きの答え合わせどおりに当たるか（10〜90%の範囲に実績の約8割が入るか）を見る。外れ方に応じて版を上げ、古い予測は残す。
3. 新しい議会報告資料「水事情について」が出たら `data/curated/storage_chart_sources.csv` に足す（2026-09-30〜10-18 の空きが埋まる）。県の主要ダムの資料からは推定しない（本人の判断）。
4. 数日分の実行記録で、18:07 の定期実行がどれだけ遅れるかをまとめて本人に見せる。
5. ライセンス（今はなし）を見直すかは本人が決める。
