"""Render the one-page static site from normalized data, forecasts and verification.

Run: ``PYTHONPATH=src python -m dam_outlook.site`` (writes ``site/index.html``)

Published values and model outputs are labelled separately on the page.
Charts are inline SVG; a small inline script adds the hover tooltip.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .forecast import load_forecasts
from .models import THRESHOLDS_PCT, read_csv
from .raw_store import JST

SCENARIO = "scenario_jma_tercile"
BASELINE = "baseline_norain"
HISTORY_DAYS = 365

DAM_ORDER = ["minamihata", "gokayama", "sefuri", "magarifuchi", "egawa", "kubara", "nagatani", "inono", "zuibaiji"]


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def fmt_pct(v) -> str:
    return f"{float(v):.2f}%" if v not in (None, "") else "—"


def fmt_m3(v) -> str:
    return f"{int(v) / 10000:,.0f}万m³" if v not in (None, "") else "—"


# ---------------------------------------------------------------- data


def load(normalized_dir: Path, forecast_dir: Path, verification_dir: Path, events_path: Path) -> dict:
    storage = read_csv(normalized_dir / "dam_storage.csv")
    total = {r["date"]: r for r in storage if r["dam_id"] == "total"}
    latest_date = max(total) if total else None
    dams = sorted(
        (r for r in storage if r["date"] == latest_date and r["dam_id"] != "total"),
        key=lambda r: DAM_ORDER.index(r["dam_id"]) if r["dam_id"] in DAM_ORDER else 99,
    )
    forecasts = load_forecasts(forecast_dir)

    def latest_of(model):
        fs = [f for f in forecasts if f["model"] == model]
        return max(fs, key=lambda f: f["issued_at"]) if fs else None

    return {
        "total": total,
        "latest_date": latest_date,
        "dams": dams,
        "scenario": latest_of(SCENARIO),
        "baseline": latest_of(BASELINE),
        "n_forecasts": len(forecasts),
        "summary": read_csv(verification_dir / "summary.csv"),
        "hindcast": read_csv(verification_dir / "hindcast.csv"),
        "events": read_csv(events_path),
        "chart_pct": chart_pct(read_csv(normalized_dir / "dam_storage_chart.csv"), total),
        "p1m": [r for r in read_csv(normalized_dir / "jma_p1m.csv") if r["element"] == "降水量" and r["period"] == "P1M"],
    }


GAP_FILL_DAYS = 3  # a gap this short between the chart readings and published values is interpolated


def chart_pct(rows: list[dict], total: dict) -> list[tuple[str, float]]:
    """Storage rate per date read off the city's report charts (median over reports), for dates not observed.

    A short gap up to the next published value is filled by linear interpolation,
    so the estimate line meets the published line.
    """
    by_date: dict[str, list[float]] = {}
    for r in rows:
        if r["series"] == "incl_reserve" and r["capacity_m3"] and r["date"] not in total:
            by_date.setdefault(r["date"], []).append(100 * int(r["storage_m3"]) / int(r["capacity_m3"]))
    est = [(d, statistics.median(v)) for d, v in sorted(by_date.items())]
    if est:
        d0, v0 = date.fromisoformat(est[-1][0]), est[-1][1]
        after = [d for d in total if d > est[-1][0] and total[d]["storage_pct"]]
        if after:
            d1 = date.fromisoformat(min(after))
            v1 = float(total[d1.isoformat()]["storage_pct"])
            gap = (d1 - d0).days
            if 1 < gap <= GAP_FILL_DAYS + 1:
                est += [((d0 + timedelta(days=i)).isoformat(), v0 + (v1 - v0) * i / gap) for i in range(1, gap)]
    return est


def estimate_series(data: dict, start: date) -> dict | None:
    """The dashed estimate line from start, ending on the first published value it leads into."""
    est = [(d, v) for d, v in data["chart_pct"] if date.fromisoformat(d) >= start]
    if not est:
        return None
    nxt = (date.fromisoformat(est[-1][0]) + timedelta(days=1)).isoformat()
    end = [(nxt, float(data["total"][nxt]["storage_pct"]))] if nxt in data["total"] else []
    return {"name": "推定値", "cls": "s1 est", "points": est + end, "hover_last": not end, "dash": True, "label": False}


# ---------------------------------------------------------------- charts


def restriction_at(events: list[dict], d: str) -> dict | None:
    """The restriction event in force on date d (the latest one on or before it)."""
    past = [e for e in events if e["date"] <= d and e.get("chikugo_intake_restriction_pct", "") != ""]
    return max(past, key=lambda e: e["date"]) if past else None


def line_chart(chart_id: str, series: list[dict], band: dict | None, y_label: str,
               restrictions: list[dict] = (), fact_end: str | None = None, narrow: bool = False) -> str:
    """SVG line chart over dates. series: {name, cls, points: [(iso, value)], dash, label, end_marker, hover_last}.

    restrictions (events.csv rows) shade the background up to fact_end with the
    Chikugo intake restriction rate in force. It is a separate measure, so it gets
    a labelled tint instead of a second y-axis.

    narrow draws the same chart in a phone-sized frame with sparser ticks and no
    end labels (the legend names the lines), so text stays readable instead of
    shrinking with the wide drawing.
    """
    w, h = (360, 280) if narrow else (720, 300)
    ml, mr, mt, mb = (34, 16, 12, 28) if narrow else (48, 64, 16, 32)
    all_pts = [p for s in series for p in s["points"]]
    if band:
        all_pts += [(d, lo) for d, lo, _ in band["points"]] + [(d, hi) for d, _, hi in band["points"]]
    if not all_pts:
        return '<p class="muted">データがまだありません。</p>'
    dates = sorted({date.fromisoformat(d) for d, _ in all_pts})
    d0, d1 = dates[0], dates[-1]
    span = max((d1 - d0).days, 1)
    vals = [v for _, v in all_pts]
    lo_v, hi_v = min(vals), max(vals)
    step = (10 if narrow else 5) if hi_v - lo_v > 20 else (5 if hi_v - lo_v > 8 else 1)
    y0 = (int(lo_v) // step) * step
    y1 = (int(hi_v) // step + 1) * step

    def x(d: str) -> float:
        return ml + (date.fromisoformat(d) - d0).days / span * (w - ml - mr)

    def y(v: float) -> float:
        return mt + (y1 - v) / (y1 - y0) * (h - mt - mb)

    out = [f'<svg id="{chart_id}" class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(y_label)}">']
    if restrictions and fact_end:
        # Segments [start, end) of constant rate within the visible, observed range.
        lo_d, hi_d = d0.isoformat(), min(fact_end, d1.isoformat())
        cuts = sorted({lo_d} | {e["date"] for e in restrictions if lo_d < e["date"] <= hi_d})
        for i, a in enumerate(cuts):
            b = cuts[i + 1] if i + 1 < len(cuts) else hi_d
            e = restriction_at(restrictions, a)
            pct = int(e["chikugo_intake_restriction_pct"]) if e else 0
            if not pct:
                continue
            x_a, x_b = x(a), max(x(b), x(a) + 2)
            note = "" if e.get("verified") == "true" else "（未確認）"
            out.append(f'<rect class="restrict" x="{x_a:.1f}" y="{mt}" width="{x_b - x_a:.1f}" height="{h - mt - mb}" '
                       f'style="opacity:{0.04 + 0.002 * pct:.3f}"/>')
            # Label as much as fits in the segment, along the top edge away from the lines;
            # the hover tooltip always has the rate.
            choices = (f'取水制限 {pct}%（{date.fromisoformat(e["date"]):%-m/%-d}〜）{note}', f"取水制限 {pct}%{note}", f"{pct}%")
            text = next((t for t in choices if x_b - x_a > (11 * len(t) if t != choices[-1] else 30)), "")
            if text:
                out.append(f'<text class="tick restrict-label" x="{x_a + 4:.1f}" y="{mt + 13}">{text}</text>')
    for v in range(y0, y1 + 1, step):
        out.append(f'<line class="grid" x1="{ml}" x2="{w - mr}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        out.append(f'<text class="tick" x="{ml - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v}%</text>')
    out.append(f'<line class="axis" x1="{ml}" x2="{w - mr}" y1="{h - mb}" y2="{h - mb}"/>')
    if span > 120:  # month starts
        every = 2 if narrow else 1
        t, i = date(d0.year, d0.month, 1), 0
        while t <= d1:
            if t >= d0:
                if i % every == 0:
                    text = f"{t.year}/1" if t.month == 1 else f"{t.month}月"
                    out.append(f'<text class="tick" x="{x(t.isoformat()):.1f}" y="{h - mb + 18}" text-anchor="middle">{text}</text>')
                i += 1
            t = date(t.year + t.month // 12, t.month % 12 + 1, 1)
    else:
        tick_every = (14 if narrow else 7) if span > 21 else (2 if span > 7 else 1)
        t = d0
        while t <= d1:
            out.append(f'<text class="tick" x="{x(t.isoformat()):.1f}" y="{h - mb + 18}" text-anchor="middle">{t.month}/{t.day}</text>')
            t += timedelta(days=tick_every)
    if band and band["points"]:
        top = " ".join(f"{x(d):.1f},{y(hi):.1f}" for d, _, hi in band["points"])
        bottom = " ".join(f"{x(d):.1f},{y(lo):.1f}" for d, lo, _ in reversed(band["points"]))
        out.append(f'<polygon class="band {band["cls"]}" points="{top} {bottom}"/>')
    labels = []
    for s in series:
        pts = s["points"]
        if not pts:
            continue
        path = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in pts)
        dash = ' stroke-dasharray="6 4"' if s.get("dash") else ""
        if len(pts) > 1:
            out.append(f'<polyline class="line {s["cls"]}" points="{path}"{dash}/>')
        # Only the latest value gets a dot; dots on every day crowd into a beaded line.
        if s.get("end_marker") or len(pts) == 1:
            for d, v in pts[-1:]:
                out.append(f'<circle class="dot {s["cls"]}" cx="{x(d):.1f}" cy="{y(v):.1f}" r="{3 if narrow else 4}"/>')
        # Series that others continue from are named in the legend only, and so is every
        # series on a phone, where end labels would take a third of the width.
        if s.get("label", True) and not narrow:
            d, v = pts[-1]
            labels.append([y(v) + 4, x(d) + 8, s["name"]])
    # Direct labels at series ends, pushed apart vertically so they never overlap.
    labels.sort()
    for i in range(1, len(labels)):
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 15)
    for ly, lx, name in labels:
        out.append(f'<text class="label" x="{lx:.1f}" y="{ly:.1f}">{esc(name)}</text>')
    out.append(f'<line class="crosshair" x1="0" x2="0" y1="{mt}" y2="{h - mb}" visibility="hidden"/>')
    out.append(f'<rect class="hit" x="{ml}" y="{mt}" width="{w - ml - mr}" height="{h - mt - mb}"/>')
    out.append("</svg>")

    # Hover data: one row per date with every series value.
    rows: dict[str, dict] = {}
    for s in series:
        for d, v in s["points"] if s.get("hover_last", True) else s["points"][:-1]:
            rows.setdefault(d, {})[s["name"]] = f"{v:.2f}%"
    if band:
        for d, lo, hi in band["points"]:
            rows.setdefault(d, {})[band["name"]] = f"{lo:.1f}–{hi:.1f}%"
    if restrictions and fact_end:
        for d in rows:
            e = restriction_at(restrictions, d) if d <= fact_end else None
            if e:
                rows[d]["筑後川取水制限"] = f'{e["chikugo_intake_restriction_pct"]}%'
    hover = {
        "x": {d: round(x(d), 1) for d in rows},
        "rows": rows,
    }
    out.append(f'<script type="application/json" data-for="{chart_id}">{json.dumps(hover, ensure_ascii=False)}</script>')
    return "\n".join(out)


def chart_pair(chart_id: str, *args, **kwargs) -> str:
    """The wide chart and its phone-sized drawing; CSS shows one of them."""
    return (f'<div class="chart-wide">{line_chart(chart_id, *args, **kwargs)}</div>\n'
            f'<div class="chart-narrow">{line_chart(chart_id + "-n", *args, narrow=True, **kwargs)}</div>')


def legend(items: list[tuple[str, str, bool]], restriction: bool = False) -> str:
    spans = []
    for name, cls, dash in items:
        spans.append(f'<span class="key"><svg width="22" height="10"><line class="line {cls}" x1="1" x2="21" y1="5" y2="5"{" stroke-dasharray=\"6 4\"" if dash else ""}/></svg>{esc(name)}</span>')
    if restriction:
        spans.append('<span class="key"><span class="swatch"></span>筑後川の取水制限</span>')
    return f'<div class="legend">{"".join(spans)}</div>'


# ---------------------------------------------------------------- sections


def _pct_series(total: dict, start: date | None = None) -> list[tuple[str, float]]:
    return [
        (d, float(r["storage_pct"]))
        for d, r in sorted(total.items())
        if r["storage_pct"] and (start is None or date.fromisoformat(d) >= start)
    ]


def md(d: str) -> str:
    return f"{int(d[5:7])}/{int(d[8:10])}"


FLAT_PT = 2  # the headline calls the outlook flat when the median is within this many points of now


def weeks_or_days(h: int) -> str:
    return f"{h // 7}週間後" if h % 7 == 0 else f"{h}日後"


def phrases(*parts: str) -> str:
    """One sentence whose lines break only between phrases, never inside a word (Japanese has no
    spaces). The sentence itself is kept on one line when it fits."""
    return '<span class="sent">' + "".join(f'<span class="ph">{p}</span>' for p in parts) + "</span>"


def trend_phrase(now_pct: float, p50: float) -> list[str]:
    """One answer per case: flat within FLAT_PT points of now, otherwise up or down."""
    diff = p50 - now_pct
    value = f'<span class="model">{p50:.0f}%前後</span>'
    if abs(diff) <= FLAT_PT:
        return ["ほぼ横ばいの", f"{value}に", "なるでしょう。"]
    return [f"{value}まで", f'{"上がる" if diff > 0 else "下がる"}でしょう。']


def no_rain_phrase(now_pct: float, v: float) -> list[str]:
    value = f'<span class="norain">{v:.0f}%</span>'
    if v < now_pct:
        return ["雨が降らなければ、", f"{value}まで", "下がります。"]
    return ["雨が降らなければ、", f"{value}です。"]


def section_now(data: dict) -> str:
    total, d = data["total"], data["latest_date"]
    if not d:
        return '<section><h2>いま</h2><p class="muted">データがまだありません。</p></section>'
    cur = total[d]
    # Change since the previous published day (none on weekends, so not always yesterday).
    prev_d = max((p for p in total if p < d and total[p]["storage_pct"]), default=None)
    delta = ""
    if prev_d:
        dv = float(cur["storage_pct"]) - float(total[prev_d]["storage_pct"])
        dm = int(cur["storage_m3"]) - int(total[prev_d]["storage_m3"])
        delta = f"<br>前回（{md(prev_d)}）から {dv:+.2f}ポイント・{dm / 10000:+,.0f}万m³"
    tiles = [f"""<div class="stat fact"><div class="stat-label">現在の貯水率 <span class="badge fact">公表値</span></div>
<div class="stat-num">{float(cur["storage_pct"]):.2f}%</div>
<div class="stat-sub">{md(d)} 9時・{fmt_m3(cur["storage_m3"])}{delta}</div></div>"""]

    sc, bl = data["scenario"], data["baseline"]
    now_pct = float(cur["storage_pct"])
    head = ""
    last = sc["forecast"][-1] if sc else None
    bl_last = bl["forecast"][-1] if bl else None
    if last:
        when = f'{weeks_or_days(last["horizon_days"])}（{md(last["date"])}）'
        head = phrases(f"{when}の", "貯水率は、", *trend_phrase(now_pct, last["storage_pct_p50"]))
        if bl_last and bl_last["horizon_days"] == last["horizon_days"]:
            head += phrases(*no_rain_phrase(now_pct, bl_last["storage_pct"]))
        tiles.append(f"""<div class="stat model"><div class="stat-label">{when}の予想 <span class="badge model">モデル出力</span></div>
<div class="stat-num">{last["storage_pct_p50"]:.0f}%前後</div></div>""")
    elif bl_last:
        head = phrases(f'{weeks_or_days(bl_last["horizon_days"])}（{md(bl_last["date"])}）まで', *no_rain_phrase(now_pct, bl_last["storage_pct"]))

    rows = "".join(
        f'<tr><td>{esc(r["dam_name"])}</td>'
        f'<td class="num"><span class="bar"><i style="width:{min(float(r["storage_pct"]), 100):.0f}%"></i></span>{fmt_pct(r["storage_pct"])}</td>'
        f'<td class="num">{fmt_m3(r["storage_m3"])}</td><td class="num">{fmt_m3(r["capacity_m3"])}</td>'
        f'<td class="num">{f"{int(r["change_m3"]) / 10000:+,.1f}万m³" if r["change_m3"] else "—"}</td></tr>'
        for r in data["dams"] if r["storage_pct"]
    )
    return f"""<section>
{f'<p class="headline">{head}</p>' if head else ""}
<div class="stats">{"".join(tiles)}</div>
<details><summary>ダム別の貯水率</summary>
<table><thead><tr><th>ダム</th><th>貯水率</th><th>貯水量</th><th>利水容量</th><th>前日比</th></tr></thead><tbody>{rows}</tbody></table>
</details>
</section>"""


def section_outlook(data: dict) -> str:
    sc, bl = data["scenario"], data["baseline"]
    f = sc or bl
    if not f:
        return '<section><h2>予想</h2><p class="muted">予想はまだ発行されていません。</p></section>'
    cap = f["inputs"]["capacity_m3"]
    base = date.fromisoformat(f["base_date"])
    hist = _pct_series(data["total"], base - timedelta(days=28))
    series = [{"name": "実績", "cls": "s1", "points": hist, "end_marker": True, "label": False}]
    est = estimate_series(data, base - timedelta(days=28))
    if est:
        series.insert(0, est)
    band = None
    if sc:
        series.append({"name": "予想", "cls": "s2", "dash": False,
                       "points": [(f["base_date"], hist[-1][1])] + [(p["date"], p["storage_pct_p50"]) for p in sc["forecast"]]})
        band = {"name": "80%の範囲", "cls": "s2",
                "points": [(p["date"], 100 * p["storage_m3_p10"] / cap, 100 * p["storage_m3_p90"] / cap) for p in sc["forecast"]]}
    if bl:
        series.append({"name": "雨なし", "cls": "s3", "dash": True,
                       "points": [(bl["base_date"], hist[-1][1])] + [(p["date"], p["storage_pct"]) for p in bl["forecast"]]})
    keys = [("実績", "s1", False)]
    if est:
        keys.append(("推定値", "s1 est", True))
    if sc:
        keys.append(("予想", "s2", False))
    if bl:
        keys.append(("雨なし", "s3", True))
    chart = chart_pair("outlook", series, band, "9ダム合計の貯水率の予想",
                       restrictions=data["events"], fact_end=data["latest_date"])

    tables = ""
    if sc:
        picks = [p for p in sc["forecast"] if p["horizon_days"] in (7, 14, 21, 28)]
        by_h = {p["horizon_days"]: p for p in bl["forecast"]} if bl else {}
        rows = "".join(
            f'<tr><td>{p["horizon_days"]}日後 <span class="muted">{md(p["date"])}</span></td>'
            f'<td class="num">{p["storage_pct_p50"]:.1f}%</td>'
            f'<td class="num">{100 * p["storage_m3_p10"] / cap:.1f}–{100 * p["storage_m3_p90"] / cap:.1f}%</td>'
            f'<td class="num">{f"{by_h[p["horizon_days"]]["storage_pct"]:.1f}%" if p["horizon_days"] in by_h else "—"}</td></tr>'
            for p in picks)
        head = "".join(f"<th class=\"num\">{p['horizon_days']}日後</th>" for p in picks)
        body = ""
        for t in THRESHOLDS_PCT:
            cells = "".join(f'<td class="num">{100 * p["prob_below_pct"][str(t)]:.0f}%</td>' for p in picks)
            body += f"<tr><td>{t}%</td>{cells}</tr>"
        p1m = sc["inputs"].get("p1m_precip")
        p1m_text = (
            f'使った気象庁1か月予報（{esc(p1m["report_datetime"][:10])} 発表、九州北部）: 降水量が少ない {p1m["below_pct"]}%・平年並 {p1m["normal_pct"]}%・多い {p1m["above_pct"]}%'
            if p1m else "1か月予報がないため、雨の多い・少ないを同じ確率で扱っています"
        )
        tables = f"""<details><summary>予想を表で見る</summary>
<div class="tables">
<div><h3>貯水率の予想</h3><table><thead><tr><th></th><th class="num">中央値</th><th class="num">80%の範囲</th><th class="num">雨なし</th></tr></thead><tbody>{rows}</tbody></table></div>
<div><h3>貯水率が下回る確率</h3><table><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table></div>
</div>
<p class="note">{p1m_text}。貯水率は利水容量 {fmt_m3(cap)} に対する割合です。</p>
</details>"""

    issued = datetime.fromisoformat(f["issued_at"]).astimezone(JST)
    models = " / ".join(f'{esc(x["model"])} v{esc(x["model_version"])}' for x in (sc, bl) if x)
    return f"""<section>
<h2>予想 <span class="badge model">モデル出力</span></h2>
{chart}
{legend(keys, restriction=True)}
<p class="sub">{issued:%Y-%m-%d %H:%M} 更新</p>
{tables}
<details><summary>計算方法</summary>
<p class="note">過去の同じ季節に降った雨を、1か月予報の確率で重み付けして使っています。</p>
{method_details(sc, bl, data["hindcast"])}
<p class="note">モデル: {models}。</p>
</details>
</section>"""


def ym(d: str) -> str:
    return f"{int(d[:4])}年{int(d[5:7])}月"


def method_details(sc: dict | None, bl: dict | None, hindcast: list[dict]) -> str:
    """How the outlook is computed, with this forecast's fitted values and the hindcast scores."""
    parts = []
    if bl:
        parts.append('<p class="note">雨なし: この先雨が降らない場合です。直近14日のうち雨のなかった日の減り方（中央値）が、この先も続くとして計算しています。</p>')
    if not sc:
        return "\n".join(parts)
    pr = sc["params"]
    quick = sum(pr["quick_m3_per_mm"])
    tau = pr["slow_tau_days"]
    slow = pr["slow_m3_per_mm"] / (tau * (1 - math.exp(-1 / tau)))  # total over the decay

    def man(v: float) -> str:
        return f"{v / 10000:.1f}万m³"

    parts.append(f"""<ol class="note">
<li>1日ごとの貯水量の増減を「水準 ＋ 雨で増える分」として計算します。雨は気象庁アメダスの福岡・前原・太宰府の日降水量の平均です。</li>
<li>雨で増える分は、{ym(pr["training_first"])}以降の貯水量と雨量（{pr["training_days"]:,}日分）から最小二乗法で推定し、予想のたびに推定し直します。今回の推定では、雨1mmあたり、その日から3日後までに計{man(quick)}、その後{tau:.0f}日ほどかけてさらに{man(slow)}増えます。貯水量が利水容量の{100 * pr["full_share"]:.0f}%を超えている日は、あふれる分があるため推定に使いません。</li>
<li>水準は、直近28日の実際の増減から雨の分を引いたものの中央値で、今回は1日あたり{man(abs(pr["level_m3_per_day"]))}{"減" if pr["level_m3_per_day"] < 0 else "増"}です。取水制限・節水・導水の効果はここに入ります。</li>
<li>この先の雨には、2006年以降の他の年の同じ時期（前後{pr["analog_half_window_days"]}日）に実際に降った28日間の雨（{pr["analog_windows_n"]:,}通り）を使います。28日間の雨の合計で少ない・平年並・多いの3つに分け、気象庁1か月予報の確率で重み付けします。</li>
<li>水準が今後ずれる分として、1日あたり平均0・標準偏差{man(pr["level_sigma_m3_per_day"])}のぶれを加え、{pr["n_sims"]:,}通り計算します。その中央値、10〜90%の範囲（80%の範囲）、閾値を下回る割合を表に示しています。</li>
</ol>
<p class="note">予想に入っていないこと: 取水制限の強化・解除などで水準が急に変わること、予想期間中の利水容量の切り替え。</p>""")
    hc = {(r["model"], r["horizon_days"]): r for r in hindcast
          if (r["model"], r["model_version"]) in ((sc["model"], sc["model_version"]), (bl["model"], bl["model_version"]) if bl else (None, None))}
    hs = [h for h in ("7", "14", "28") if (sc["model"], h) in hc]
    if hs:
        first = hc[(sc["model"], hs[0])]
        rows = ""
        for h in hs:
            r = hc[(sc["model"], h)]
            b = hc.get((bl["model"], h)) if bl else None
            rows += (f'<tr><td>{h}日後</td><td class="num">{fmt_m3(r["mae_m3"])}</td>'
                     f'<td class="num">{fmt_m3(b["mae_m3"]) if b else "—"}</td>'
                     f'<td class="num">{fmt_m3(r["crps_approx_m3"])}</td>'
                     f'<td class="num">{100 * float(r["p10_p90_coverage"]):.0f}%</td></tr>')
        parts.append(f"""<p class="note">過去にさかのぼった答え合わせ: {ym(first["first_base"])}〜{ym(first["last_base"])}の毎週（{esc(first["n"])}回）について、その日までのデータだけで予想し直し、実績と比べました。雨のシナリオからはその年を除き、当時の1か月予報は残っていないため3つの区分を同じ重みにしています。</p>
<table><thead><tr><th></th><th>中央値の誤差</th><th>雨なしの誤差</th><th>CRPS</th><th>80%の範囲に入った割合</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note">誤差は貯水量の平均絶対誤差です。CRPS は範囲も含めた予想全体の外れ具合で、小さいほど良く、雨なしの計算では誤差と同じ値になります。この期間は雨が少なかったため、中央値は雨なしの計算より外れました。</p>""")
    parts.append('<p class="note">発行した予想は、使った入力と一緒に書き換えずに保存し、下の「答え合わせ」で実績と比べます。</p>')
    return "\n".join(parts)


def section_history(data: dict) -> str:
    if not data["latest_date"]:
        return ""
    start = date.fromisoformat(data["latest_date"]) - timedelta(days=HISTORY_DAYS)
    pts = _pct_series(data["total"], start)
    est = estimate_series(data, start)
    series = [{"name": "実績", "cls": "s1", "points": pts, "end_marker": True}]
    if est:
        series.insert(0, est)
    chart = chart_pair("history", series, None, "9ダム合計の貯水率の推移",
                       restrictions=data["events"], fact_end=data["latest_date"])
    keys = legend([("実績", "s1", False)] + ([("推定値", "s1 est", True)] if est else []), restriction=True)
    rows = "".join(
        f'<tr><td>{esc(d)}</td><td class="num">{float(r["storage_pct"]):.2f}%</td><td class="num">{fmt_m3(r["storage_m3"])}</td>'
        f'<td class="num">{fmt_m3(r["capacity_m3"])}</td><td>{esc(r["sources"])}</td></tr>'
        for d, r in sorted(data["total"].items(), reverse=True)
    )
    return f"""<section>
<h2>推移 <span class="badge fact">公表値</span></h2>
{chart}
{keys}
<details><summary>貯水率を表で見る</summary>
<table><thead><tr><th>日付</th><th>貯水率</th><th>貯水量</th><th>利水容量</th><th>出典</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note">利水容量は季節で変わるため、貯水量が増えても貯水率が下がる日があります（例: 2026-10-21 に南畑ダムの容量が 365万→512万m³）。</p>
</details>
{restriction_details(data["events"])}
</section>"""


def section_verification(data: dict) -> str:
    s = data["summary"]
    if not s:
        body = f'<p class="muted">予想した日の公表値が出ると、ここで答え合わせします（発行済みの予想 {data["n_forecasts"]} 件）。</p>'
    else:
        rows = "".join(
            f'<tr><td>{esc(r["model"])} v{esc(r["model_version"])}</td><td>{esc(r["horizon"])}日</td><td class="num">{esc(r["n"])}</td>'
            f'<td class="num">{fmt_m3(r["mae_m3"])}</td><td class="num">{esc(r["mae_skill"]) or "—"}</td>'
            f'<td class="num">{esc(r["p10_p90_coverage"]) or "—"}</td><td class="num">{esc(r["brier_mean"]) or "—"}</td></tr>'
            for r in s
        )
        body = f"""<table><thead><tr><th>モデル</th><th>何日先</th><th>件数</th><th>平均絶対誤差</th><th>雨なし比スキル</th><th>80%範囲の的中率</th><th>Brier</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note">雨なし比スキル = 1 − 誤差 ÷ 雨が降らない場合の誤差（0より大きければ良い）。80%範囲の的中率は 0.8 に近いほど適切です。Brier は閾値を下回る確率の誤差です（0 が最良）。</p>"""
    return f'<section><h2>答え合わせ <span class="badge model">モデル評価</span></h2>{body}</section>'


def restriction_details(events: list[dict]) -> str:
    rows = "".join(
        f'<tr><td>{esc(e["date"])}</td><td>{esc(e["detail"])}</td><td class="num">{esc(e["chikugo_intake_restriction_pct"])}%</td>'
        f'<td>{"確認済み" if e.get("verified") == "true" else "<span class=\"badge warn\">未確認</span>"}</td>'
        f'<td>{esc(urlparse(e["source"]).netloc) if e.get("source") else "出典なし"}</td></tr>'
        for e in sorted(events, key=lambda e: e["date"], reverse=True)
    )
    if not rows:
        return ""
    unverified = ('<p class="note">未確認の行は、一次資料と照合していません。</p>'
                  if any(e.get("verified") != "true" for e in events) else "")
    return f"""<details><summary>筑後川の取水制限の経過</summary>
<p class="note">福岡地区水道企業団の筑後川からの取水制限率です（公表値）。</p>
<table><thead><tr><th>実施日</th><th>内容</th><th>制限率</th><th>確認</th><th>出典</th></tr></thead><tbody>{rows}</tbody></table>
{unverified}
</details>"""


# ---------------------------------------------------------------- page

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--warn:#fab219}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 8px;display:flex;gap:8px;align-items:center}
section{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px;margin:16px 0}
.sub,.note,.muted{color:var(--ink2);font-size:13px;margin:4px 0}.muted{color:var(--muted)}
h3{font-size:14px;margin:12px 0 0}
.headline{font-size:20px;font-weight:600;line-height:1.55;margin:0 0 12px}
.headline .sent,.headline .ph{display:inline-block}.headline .model{color:var(--s2)}.headline .norain{color:var(--s3)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px 24px;margin:8px 0}
.stat{border-left:3px solid var(--s1);padding:2px 0 2px 12px}.stat.model{border-left-color:var(--s2)}
.stat-label{font-size:13px;color:var(--ink2);display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.stat-num{font-size:48px;font-weight:600;line-height:1.25;font-variant-numeric:tabular-nums}
.stat-sub{font-size:13px;color:var(--ink2)}.stat.model .stat-num{color:var(--s2)}
@media (max-width:600px){.stat-num{font-size:40px}}
.bar{display:inline-block;width:56px;height:8px;margin-right:8px;background:var(--grid);border-radius:4px;overflow:hidden;vertical-align:middle}
.bar i{display:block;height:100%;background:var(--s1)}
.tables{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:0 24px}
.badge{font-size:11px;font-weight:600;padding:1px 8px;border-radius:10px;border:1px solid var(--border);color:var(--ink2)}
.badge.fact{border-color:var(--s1)}.badge.model{border-color:var(--s2)}.badge.warn{border-color:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;display:block;overflow-x:auto}
th,td{padding:4px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums}
ol.note{padding-left:20px}ol.note li{margin:2px 0}
details summary{cursor:pointer;color:var(--ink2);font-size:13px;margin-top:8px}
a{color:var(--s1)}
.chart{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .axis{stroke:var(--axis);stroke-width:1}
.chart .ref{stroke:var(--muted);stroke-width:1;stroke-dasharray:2 3}
.chart .tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.chart .label{fill:var(--ink2);font-size:12px}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.line.s1{stroke:var(--s1)}.line.est{stroke-width:1.5;opacity:.8}.line.s2{stroke:var(--s2)}.line.s3{stroke:var(--s3)}
.dot{stroke:var(--surface);stroke-width:2}.dot.s1{fill:var(--s1)}.dot.s2{fill:var(--s2)}.dot.s3{fill:var(--s3)}
.band.s2{fill:var(--s2);opacity:.18}
.restrict{fill:var(--warn)}.chart .restrict-label{fill:var(--ink2)}
.crosshair{stroke:var(--axis);stroke-width:1}.hit{fill:transparent}
.swatch{display:inline-block;width:14px;height:10px;background:var(--warn);opacity:.4;border-radius:2px}
.chart-narrow{display:none}@media (max-width:600px){.chart-wide{display:none}.chart-narrow{display:block}}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--ink2);margin:8px 0}
.key{display:inline-flex;align-items:center;gap:6px}
.tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.12);display:none;z-index:10}
.tip b{display:block;margin-bottom:2px}
footer{color:var(--muted);font-size:12px;margin-top:24px}footer p{margin:4px 0}
"""

JS = """
(()=>{const tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
document.querySelectorAll('script[data-for]').forEach(s=>{const svg=document.getElementById(s.dataset.for);if(!svg)return;
const d=JSON.parse(s.textContent),keys=Object.keys(d.x).sort(),hit=svg.querySelector('.hit'),ch=svg.querySelector('.crosshair');
function at(ev){const pt=svg.createSVGPoint();pt.x=ev.clientX;pt.y=ev.clientY;const p=pt.matrixTransform(svg.getScreenCTM().inverse());
let best=keys[0];for(const k of keys){if(Math.abs(d.x[k]-p.x)<Math.abs(d.x[best]-p.x))best=k}return best}
function show(ev){const k=at(ev);ch.setAttribute('x1',d.x[k]);ch.setAttribute('x2',d.x[k]);ch.setAttribute('visibility','visible');
tip.innerHTML='<b>'+k+'</b>'+Object.entries(d.rows[k]).map(([n,v])=>n+': '+v).join('<br>');tip.style.display='block';
const x=Math.min(ev.clientX+12,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(ev.clientY+12)+'px'}
function hide(){tip.style.display='none';ch.setAttribute('visibility','hidden')}
hit.addEventListener('pointermove',show);hit.addEventListener('pointerdown',show);hit.addEventListener('pointerleave',hide)})})();
"""


def render(data: dict, now: datetime) -> str:
    body = "\n".join([section_now(data), section_outlook(data), section_history(data), section_verification(data)])
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="福岡市関連9ダム合計の貯水率の推移と、28日先までの確率つきの予想。"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><title>福岡市ダム貯水率の予想サイト</title><style>{CSS}</style></head>
<body><main>
<h1>福岡市ダム貯水率の予想サイト</h1>
<p class="sub">公表されたデータと、それを元にした独自の予想を趣味で掲載しています。</p>
{body}
<footer>
<p>{now:%Y-%m-%d %H:%M} 更新。福岡市水道局・気象庁とは関係ありません。内容の正確さは保証しません。</p>
<p>出典: 福岡市水道局「きょうのダム状況（テキスト版）」「浄水日報」、市議会への報告資料「水事情について」。<br>
出典: 福岡市「福岡市関連９ダム貯水量」（BODIK、CC BY）を加工して作成。<br>
出典: 気象庁ホームページ（<a href="https://www.jma.go.jp/">https://www.jma.go.jp/</a>）の1か月予報・アメダス観測値・過去の気象データ。これらを加工して作成しており、編集・加工の責任は本サイトにあります。</p>
<p>計算のコード・発行した予想・答え合わせの結果: <a href="https://github.com/yohei-washizaki/fukuoka-dam-outlook">GitHub</a></p>
</footer>
</main><script>{JS}</script></body></html>
"""


# A reservoir behind a dam wall; drawn to stay legible at 16px.
# assets/apple-touch-icon.png is the same drawing without the rounded corners (iOS adds its own):
# rsvg-convert -w 180 -h 180 of FAVICON with rx="7" removed.
FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><clipPath id="c"><rect width="32" height="32" rx="7"/></clipPath><g clip-path="url(#c)"><rect width="32" height="32" fill="#1f5fae"/><path d="M0 15c3-1.6 5-1.6 8 0s5 1.6 8 0l4-.6V32H0z" fill="#8cc4ff"/><path d="M19 8h4.5L31 32H19z" fill="#f0efec"/></g></svg>
"""

NOT_FOUND = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><title>ページがありません</title>
<style>body{margin:0;padding:24px 16px;font:15px/1.6 system-ui,sans-serif;background:#f9f9f7;color:#0b0b0b}
@media (prefers-color-scheme:dark){body{background:#0d0d0d;color:#f0efec}a{color:#3987e5}}</style></head>
<body><p>ページが見つかりません。<a href="/">トップへ</a></p></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--normalized-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--forecast-dir", type=Path, default=Path("data/forecasts"))
    parser.add_argument("--verification-dir", type=Path, default=Path("data/verification"))
    parser.add_argument("--events", type=Path, default=Path("data/curated/events.csv"))
    parser.add_argument("--out", type=Path, default=Path("site/index.html"))
    args = parser.parse_args(argv)
    data = load(args.normalized_dir, args.forecast_dir, args.verification_dir, args.events)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data, datetime.now(JST)), encoding="utf-8")
    (args.out.parent / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    # Cloudflare Pages serves index.html for unknown paths unless a 404.html exists.
    (args.out.parent / "404.html").write_text(NOT_FOUND, encoding="utf-8")
    (args.out.parent / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    (args.out.parent / "apple-touch-icon.png").write_bytes((Path(__file__).parent / "assets" / "apple-touch-icon.png").read_bytes())
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
