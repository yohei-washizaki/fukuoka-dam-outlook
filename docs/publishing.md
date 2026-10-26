# Publishing

## Decision (2026-10-25)

- 配信先は Cloudflare Pages（プロジェクト `dam-outlook`、`https://dam-outlook.pages.dev/`）の Git 連携。`main` への push（Actions の自動コミットを含む）ごとに Cloudflare が `PYTHONPATH=src python3 -m dam_outlook.site` を実行し、`site/`（`index.html` と `robots.txt`）だけを配信する。Cloudflare の GitHub アプリはリポジトリ全体を読めるが、公開されるのは `site/` だけ。API トークンは使わない。
- プレビューは `preview/*` のブランチだけ（`<branch>.dam-outlook.pages.dev`、誰でも見られる）。Dependabot などのブランチはプレビューされない。
- このリポジトリは private のまま（`data/raw/` に福岡市のページ・PDF の写しがあるため）。
- 福岡市水道局には問い合わせない。代わりに、市のページへはリンクせず、出典は名称だけを書く。
- 2026-10-25〜26 は `noindex,nofollow` と `robots.txt` で検索に出さなかった。気象庁の回答（下の Open）を受けて、2026-10-26 に外した。404 ページは `noindex` のまま。プレビューは Cloudflare が `X-Robots-Tag: noindex` を付ける。

## Public copy (2026-10-26)

- 第三者が計算方法と精度を確かめられるように、公開用のリポジトリ `yohei-washizaki/fukuoka-dam-outlook` に、コード・テスト・docs・`data/normalized/`・`data/forecasts/`・`data/verification/`・`events.csv`・`storage_chart_sources.csv` を書き出す（`dam_outlook.export`）。
- 書き出さないもの: `data/raw/`、市のページの写しのテスト素材（`city_dam_text.html`・`nippou_2026_10_21.txt`。それを使うテストは公開側でスキップ）、出来事の照合メモ、`.github/`。福岡市の個別ページの URL は `https://www.city.fukuoka.lg.jp/...` に置き換える（2026-10-26 に決定。個別ページへのリンクには担当課への問い合わせが必要なため）。
- `.github/workflows/public.yml` が、`fetch` の後と main への push の後に同期する。公開用リポジトリの書き込み専用デプロイキーを、このリポジトリの secret `PUBLIC_REPO_DEPLOY_KEY` に置く。未設定なら何もしない。
- 2026-10-26 に public にした（https://github.com/yohei-washizaki/fukuoka-dam-outlook）。ライセンスは付けない（閲覧と検証のための実行はできるが、再利用は認めない）。気象庁の回答を見てから MIT などにするか決める。
- 公開用リポジトリの履歴は同期コミットだけで、このリポジトリの履歴は持ち出さない（過去のコミットに `data/raw/` があるため）。

## Open

- 予報業務許可の要否を、気象庁 情報基盤部 情報利用推進課（予報業務許可の相談窓口）に問い合わせた（2026-10-25 22:13 JST に本人が送信）。2026-10-26 13:07 JST の回答: ダム貯水量（貯水率）の見通しを反復して公表することは、気象業務法第17条の予報業務に**該当しない**。注意点は、資料の出典を資料提供元のルールに従って書くこと、気象庁コンテンツは「気象庁ホームページについて」（https://www.jma.go.jp/jma/kishou/info/coment.html）に従うこと。これを受けて `noindex` を外した。
- 洪水の予報業務許可の手引き（令和8年9月版、https://www.jma.go.jp/jma/kishou/minkan/tebiki/kouzui_tebiki.pdf）では、「洪水」を河川の増水とそれによる氾濫とし、「河川の水位等の予想を反復・継続して発表することは、営利か非営利かを問わず許可の対象」としている。dam-outlook は河川の水位・流量やダムの流入量を予想・表示しない。
- 類似の事例（2026-10-25 の調査）: ダム貯水量の将来予測を一般向けに公開している民間・個人のサイトは見つからなかった。現在値を表示するサイト（DamViewer、Dam Data Japan など）と、国や水系の協議会が発表する無降雨の見通しはある。

## Terms checked (2026-10-25)

- 気象庁ホームページ（https://www.jma.go.jp/jma/kishou/info/coment.html）: 公共データ利用規約（第1.0版）に準拠。出典の記載、加工した場合はその旨の記載が必要で、国が作成したかのような態様で公表してはいけない。留意事項（https://www.data.jma.go.jp/developer/ryuui.pdf）では、加工した場合は編集責任が編集者にあることも明記するよう求めている。リンクは自由。
- 予報業務許可（気象業務法17条）: 許可の対象は気象等・地震動・火山・土砂崩れ・津波・高潮・波浪・洪水の予想（https://www.jma.go.jp/jma/kishou/minkan/q_a_m.html 、https://www.jma.go.jp/jma/kishou/minkan/q_a_o.html）。ダム貯水量はこの一覧に無く、対象外と読んでいた。2026-10-26 に気象庁から、該当しないと回答があった（Open を参照）。このため、モデル内部の降水量シナリオはページに出さず、気象庁の確率は加工せずに出典つきで示す。
- 福岡市水道局（https://www.city.fukuoka.lg.jp/...）: 掲載情報の著作権は市に帰属し、私的使用・引用等を除き無断の複製・転用は不可。トップページへのリンクは自由だが管理者への連絡を求めている。個別ページへのリンクは各担当課への問い合わせが必要。公表値（数値）の掲載は事実の利用と考えている（解釈）。
- GitHub Pages は Free プランでは public リポジトリのみ（https://docs.github.com/get-started/learning-about-github/githubs-products）。

## Setup

1. GitHub の Settings → Applications → Installed GitHub Apps → Cloudflare Workers and Pages → Configure で、Repository access に `dam-outlook` を追加する。
2. Cloudflare Pages のプロジェクトを Git 連携で作る（API で作成。ビルドコマンド `PYTHONPATH=src python3 -m dam_outlook.site`、出力 `site`、`PYTHON_VERSION=3.12`、プレビューは `preview/*` のみ）。
