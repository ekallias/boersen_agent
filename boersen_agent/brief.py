"""The daily brief as a one-page HTML/PDF (layout from the Börsen-Agent brief template).

`daily_data()` turns the report context into the brief's data; `render()` lays it out as HTML and
`write_pdf()` prints it with headless Chrome (or WeasyPrint if installed). Fixed labels come from
i18n, so the brief follows the configured report language.
"""
from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

from .i18n import _, action, date_long, date_short, num, reason
from .names import nice_name

# --- design tokens -----------------------------------------------------------------
PAPER, INK, MUTED, RULE, TRACK = "#F6F4EE", "#14171C", "#5B616B", "#CFCABB", "#E4E0D5"
BODY = "#2A2F37"
NAVY, RED, AMBER, GREEN = "#1F3A5F", "#B3261E", "#B97A00", "#1E6B4A"
STOP_GREY = "#8A8F98"
PAGE_W, PAGE_H = 1000, 2000

SERIF = "'Fraunces', Georgia, serif"
SANS = "'Inter', system-ui, sans-serif"

TONES = {"ink": INK, "green": GREEN, "red": RED}
from .report import MAX_DAILY_ACTIONS as MAX_ACTIONS  # noqa: E402 - one source for the cap
CHART_WEEKS = 53  # about one year of weekly closes


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt_minus(text: str) -> str:
    """Use a true minus sign for display."""
    return text.replace(" -", " −").replace("(-", "(−")


# --- sparkline ---------------------------------------------------------------------
def sparkline(closes, stops, today=None, colour=NAVY, w=230, h=54, pad=5, label="") -> str:
    """Inline SVG: weekly closes (solid), rolling stop (dashed), optional live price ring."""
    n = len(closes)
    ahead = 2 if today is not None else 0
    values = list(closes) + list(stops) + ([today] if today is not None else [])
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0

    def x(i: float) -> float:
        return pad + i * (w - 2 * pad) / (n - 1 + ahead)

    def y(v: float) -> float:
        return pad + (hi - v) * (h - 2 * pad) / span

    def pts(vals):
        return " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))

    last_x, last_y = x(n - 1), y(closes[-1])
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}" '
        f'style="display: block; overflow: visible">',
        f'<polyline points="{pts(stops)}" fill="none" stroke="{STOP_GREY}" stroke-width="1.3" '
        f'stroke-dasharray="3 3" stroke-linejoin="round"/>',
        f'<polyline points="{pts(closes)}" fill="none" stroke="{NAVY}" stroke-width="1.8" '
        f'stroke-linejoin="round" stroke-linecap="round"/>',
    ]
    if today is not None:
        tx, ty = x(n - 1 + ahead), y(today)
        parts.append(f'<line x1="{last_x:.1f}" y1="{last_y:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" '
                     f'stroke="{colour}" stroke-width="1.3" stroke-dasharray="2 2"/>')
        parts.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3.4" fill="{PAPER}" stroke="{colour}" '
                     f'stroke-width="1.8"/>')
    parts.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="{colour}"/>')
    parts.append("</svg>")
    return "".join(parts)


# --- building blocks ---------------------------------------------------------------
def kpi(k: dict, first: bool, last: bool) -> str:
    pad = "padding-right: 20px" if first else ("padding-left: 20px" if last else "padding: 0 20px")
    border = "" if last else f"border-right: 1px solid {RULE};"
    return (
        f'<div style="{pad}; {border} display: flex; flex-direction: column; gap: 4px">'
        f'<div style="font-size: 11px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase; color: {MUTED}">{esc(k["label"])}</div>'
        f'<div style="font-family: {SERIF}; font-size: 38px; font-weight: 500; line-height: 1.1; color: {TONES.get(k.get("tone", "ink"), INK)}">{esc(k["value"])}</div>'
        f'<div style="font-size: 12px; color: {MUTED}">{esc(k["sub"])}</div>'
        f"</div>"
    )


def chip(c: dict) -> str:
    positive = c["sign"] == "+"
    bg, fg = ("#DCEBE2", "#14502F") if positive else ("#F4DAD7", "#8C1A13")
    sign = "+" if positive else "−"
    return (f'<div style="padding: 5px 12px; border-radius: 999px; background: {bg}; color: {fg}; '
            f'font-size: 12.5px; font-weight: 600">{sign} {esc(c["text"])}</div>')


def action_row(a: dict, labels: dict) -> str:
    is_sell = a["action"].upper() == "SELL"
    pct = float(a.get("above_stop_pct") or 0)
    if is_sell:
        fill, tone = RED, RED
        badge_bg, badge_fg = RED, "#FFFFFF"
    elif pct < 15:
        fill, tone = AMBER, "#8A5700"
        badge_bg, badge_fg = "#F1DFB8", "#5E3C00"
    else:
        fill, tone = NAVY, INK
        badge_bg, badge_fg = "#F1DFB8", "#5E3C00"
    width = max(2.0, min(pct / 40 * 100, 100))
    if a.get("closes"):
        label = f'{labels["weekly_closes"]} {a["name"]}, {a["closes"][-1]:g}, stop {a["stops"][-1]:g}'
        spark = sparkline(a["closes"], a["stops"], a.get("today"), colour=fill, label=label)
    else:
        spark = f'<div style="font-size: 12px; color: {MUTED}">{esc(labels["no_history"])}</div>'
    return (
        f'<div style="display: grid; grid-template-columns: 120px minmax(0, 1fr) 230px; gap: 24px; '
        f'padding: 16px 0; border-bottom: 1px solid {RULE}; align-items: start">'
        f'<div style="display: flex; flex-direction: column; gap: 6px">'
        f'<div style="display: inline-flex; align-self: flex-start; padding: 4px 12px; background: {badge_bg}; '
        f'color: {badge_fg}; font-size: 12px; font-weight: 600; letter-spacing: 0.1em">{esc(action(a["action"]).upper())}</div>'
        f'<div style="font-size: 12px; color: {MUTED}">{esc(labels["confidence"])} <span style="font-weight: 600; color: {INK}">{esc(a["confidence"])}</span></div>'
        f"</div>"
        f'<div style="display: flex; flex-direction: column; gap: 3px">'
        f'<div style="font-weight: 600; font-size: 15px">{esc(a["name"])}</div>'
        f'<div style="font-size: 12px; color: {MUTED}">{esc(fmt_minus(a["sub"]))}</div>'
        + (f'<div style="color: {RED}; font-size: 12px; font-weight: 600">⚠ {esc(a["override"])}</div>' if a.get("override") else "")
        + f'<div style="color: {BODY}">{esc(a["text"])}</div>'
        f"</div>"
        f'<div style="display: flex; flex-direction: column; gap: 6px">'
        f"{spark}"
        f'<div style="display: flex; justify-content: space-between; font-size: 12px">'
        f'<span style="color: {MUTED}">{esc(labels["above_stop"])}</span><span style="font-weight: 600; color: {tone}">{'+' if pct >= 0 else '−'}{abs(pct):g}%</span></div>'
        f'<div style="height: 8px; background: {TRACK}; border-radius: 4px; overflow: hidden">'
        f'<div style="width: {width:.2f}%; height: 8px; background: {fill}"></div></div>'
        f'<div style="font-size: 12px; color: {MUTED}">{esc(a["caption"])}</div>'
        f"</div>"
        f"</div>")


def watch_item(w: dict, last: bool) -> str:
    border = "" if last else f"border-bottom: 1px solid {RULE};"
    return (f'<div style="padding: 10px 0; {border} display: flex; flex-direction: column; gap: 3px">'
            f'<div style="font-weight: 600">{esc(w["title"])}</div>'
            f'<div style="color: {BODY}; font-size: 13px">{esc(w["text"])}</div>'
            f'<div style="font-size: 13px; color: {NAVY}; font-weight: 500">→ {esc(w["next"])}</div>'
            f"</div>")


def alloc_row(r: dict) -> str:
    actual, target = float(r["actual"]), r.get("target")
    right = f"{actual:.1f}%" + (f" · target {target:g}%" if target is not None else "")
    bar_colour = STOP_GREY if target is None else NAVY
    fill = (f'<div style="width: {min(actual, 100):.1f}%; height: 10px; background: {bar_colour}; '
            f'border-radius: 5px 0 0 5px"></div>' if actual > 0 else "")
    tick = (f'<div style="position: absolute; left: {target:g}%; top: -3px; width: 2px; height: 16px; '
            f'background: {INK}"></div>' if target is not None else "")
    return (f'<div style="display: flex; flex-direction: column; gap: 5px">'
            f'<div style="display: flex; justify-content: space-between; font-size: 13px">'
            f'<span style="font-weight: 600">{esc(r["label"])}</span><span style="color: {MUTED}">{esc(right)}</span></div>'
            f'<div style="position: relative; height: 10px; background: {TRACK}; border-radius: 5px">{fill}{tick}</div>'
            f"</div>")


def h2(text: str, extra: str = "") -> str:
    return (f'<h2 style="margin: 0; font-family: {SERIF}; font-weight: 500; font-size: 26px; line-height: 1.15{extra}">'
            f"{esc(text)}</h2>")


# --- page --------------------------------------------------------------------------
def render(d: dict) -> str:
    m = d["meta"]
    labels = d["labels"]
    kpis = "".join(kpi(k, i == 0, i == len(d["kpis"]) - 1) for i, k in enumerate(d["kpis"]))
    chips = "".join(chip(c) for c in d["summary"]["chips"])
    rows = "".join(action_row(a, labels) for a in d["actions"])
    watch = "".join(watch_item(w, i == len(d["watch"]) - 1) for i, w in enumerate(d["watch"]))
    alloc = d["allocation"]
    alloc_rows = "".join(alloc_row(r) for r in alloc["rows"])
    return f"""<!doctype html>
<html lang="{esc(d.get("lang", "en"))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={PAGE_W}">
<title>{esc(m["eyebrow"])} · {esc(m["date"])}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&amp;family=Inter:wght@400;500;600&amp;display=swap">
<style>
@page {{ size: {PAGE_W}px {PAGE_H}px; margin: 0 }}
html, body {{ margin: 0; background: {PAPER}; -webkit-print-color-adjust: exact; print-color-adjust: exact }}
</style>
</head>
<body>
<div style="width: {PAGE_W}px; height: {PAGE_H}px; box-sizing: border-box; padding: 48px 56px 40px; background: {PAPER}; color: {INK}; font-family: {SANS}; font-size: 14px; line-height: 1.45; display: flex; flex-direction: column; gap: 26px; font-variant-numeric: tabular-nums">

<div style="display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 20px; border-bottom: 2px solid {INK}">
<div style="display: flex; flex-direction: column; gap: 6px">
<div style="font-size: 12px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; color: {MUTED}">{esc(m["eyebrow"])}</div>
<h1 style="margin: 0; font-family: {SERIF}; font-weight: 500; font-size: 52px; line-height: 1.05; letter-spacing: -0.01em">{esc(m["date"])}</h1>
</div>
<div style="text-align: right; display: flex; flex-direction: column; gap: 2px">
<div style="font-family: {SERIF}; font-size: 28px; font-weight: 500">{esc(m["time"])}</div>
<div style="font-size: 12px; color: {MUTED}">{esc(m["issue"][0])}</div>
<div style="font-size: 12px; color: {MUTED}">{esc(m["issue"][1])}</div>
</div>
</div>

<div style="display: grid; grid-template-columns: repeat({len(d["kpis"])}, minmax(0, 1fr)); border-bottom: 1px solid {RULE}; padding-bottom: 22px">{kpis}</div>

<div style="display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 40px; align-items: start">
<div style="display: flex; flex-direction: column; gap: 8px">
{h2(d["summary"]["title"])}
<p style="margin: 0; font-size: 14.5px; color: {BODY}">{esc(d["summary"]["text"])}</p>
</div>
<div style="display: flex; flex-wrap: wrap; gap: 8px; align-content: flex-start; padding-top: 4px">{chips}</div>
</div>

<div style="display: flex; flex-direction: column">
<div style="display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 10px; border-bottom: 2px solid {INK}">
{h2(labels["action_items"])}
<div style="font-size: 12px; color: {MUTED}">{esc(d["actions_header"])}</div>
</div>
{rows}
</div>

<div style="display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 40px; align-items: start">
<div style="display: flex; flex-direction: column">
{h2(labels["watch"], "; margin-bottom: 10px; padding-bottom: 10px; border-bottom: 2px solid " + INK)}
{watch}
</div>
<div style="display: flex; flex-direction: column">
{h2(labels["allocation"], "; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 2px solid " + INK)}
<div style="display: flex; flex-direction: column; gap: 16px">{alloc_rows}</div>
<div style="margin-top: 14px; font-size: 11.5px; color: {MUTED}; display: flex; flex-direction: column; gap: 6px">
<div><span style="display: inline-block; width: 2px; height: 10px; background: {INK}; margin-right: 6px; vertical-align: -1px"></span>{esc(alloc["legend"])}</div>
<div>{esc(alloc["note"])}</div>
</div>
</div>
</div>

<div style="margin-top: auto; padding-top: 14px; border-top: 1px solid {RULE}; font-size: 11px; line-height: 1.5; color: {MUTED}">{esc(d["footer"])}</div>

</div>
</body>
</html>
"""


# --- PDF ---------------------------------------------------------------------------
def write_pdf(html_path: Path, pdf_path: Path) -> str:
    for exe in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(exe)
        if found:
            subprocess.run([found, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()],
                           check=True, capture_output=True, timeout=120)
            return exe
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        raise RuntimeError("no PDF backend: install Chrome/Chromium or weasyprint") from e
    HTML(filename=str(html_path)).write_pdf(str(pdf_path))
    return "weasyprint"


# --- data --------------------------------------------------------------------------
def _labels() -> dict:
    return {
        "confidence": _("Confidence"),
        "above_stop": _("Above stop"),
        "action_items": _("Action items"),
        "watch": _("What to watch today"),
        "allocation": _("Allocation"),
        "weekly_closes": _("Weekly closes for"),
        "no_history": _("No price history available"),
    }


def _series(closes_full: list[float], stop_weeks: int) -> tuple[list[float], list[float]]:
    """The last CHART_WEEKS closes and their rolling stop (lowest close of the previous stop_weeks)."""
    n = len(closes_full)
    start = max(0, n - CHART_WEEKS)
    stops = []
    for j in range(start, n):
        window = closes_full[max(0, j - stop_weeks):j]
        stops.append(min(window) if window else None)
    first = next((s for s in stops if s is not None), None)
    if first is None:
        return [], []
    stops = [s if s is not None else first for s in stops]
    return closes_full[start:], stops


def _chips(ms) -> list[dict]:
    chips = []
    for key in ("zinsstruktur", "index_trend", "anleihezinsen", "oel", "crb", "dollar", "saison", "short_term"):
        c = ms.components.get(key)
        if c is None or not c.state:  # None = unknown, 0 = neutral: no chip
            continue
        chips.append({"sign": "+" if c.state > 0 else "-", "text": _(c.label)})
    g = ms.gesamt_score
    if g:
        chips.insert(0, {"sign": "+" if g[0] > g[1] else "-", "text": f"Gesamtsystem {g[0]}:{g[1]}"})
    return chips[:7]


def daily_data(ctx, analysis, now, items, closes_for, targets: dict, shown_total: int, stop_weeks: int,
               language: str = "en") -> dict:
    """Build the brief's data from the report context.

    items: the (suggestion, call, action) tuples to show (already limited to MAX_ACTIONS).
    closes_for(symbol) -> list of completed weekly closes (oldest first), or None.
    """
    port, ms = ctx.portfolio, ctx.market
    total = port["total_eur"] if port["total_eur"] is not None else port["known_total_eur"]
    safe = ms.safety_effective
    regime = _("positive" if safe == 1 else "negative" if safe == -1 else "unknown")
    kpis = [
        {"label": _("Portfolio"), "value": f"{num(total, 0)} €" if total else "–",
         "sub": _("all positions priced") if port["total_eur"] is not None else _("incomplete"), "tone": "ink"},
        {"label": _("Equity share"), "value": f"{num(port['equity_share'] or 0, 0)}%",
         "sub": _("stocks + known fund exposure"), "tone": "ink"},
        {"label": _("Market regime"), "value": regime.capitalize(), "sub": "Sicherheitssystem",
         "tone": "green" if safe == 1 else "red" if safe == -1 else "ink"},
        {"label": _("Actions"), "value": str(len(items)),
         "sub": (_("top {n} of {total}").format(n=len(items), total=shown_total) if shown_total > len(items)
                 else _("position actions")),
         "tone": "red" if items else "green"},
    ]
    actions = []
    for s, call, code in items:
        live = s.live or {}
        full = closes_for(s.symbol) if s.symbol else None
        closes, stops = _series(full, stop_weeks) if full else ([], [])
        price = live.get("price")
        dist = live.get("dist_to_stop_pct")
        stop = live.get("stop")
        sub = " · ".join(x for x in (s.symbol, s.isin) if x)
        override = None
        if call:
            text = call["rationale"]
            confidence = call["confidence"]
            if call.get("overrides_9m_sell") or call.get("overrides_stated_rule"):
                # guardrail: a call that differs from a stated newsletter rule is always flagged
                rule_label = action(call.get("rule_action") or s.action)
                override = f"{_('differs from the rule')} ({rule_label}): {call.get('override_reason') or ''}"
        else:
            text = reason(s.reasons[0]["text"]) if s.reasons else ""
            confidence = s.confidence
        caption = (f"{_('Price')} {num(price, 2)} · {_('stop')} {num(stop, 2)}"
                   if price is not None and stop is not None else _("No live price"))
        actions.append({
            "action": code, "confidence": confidence, "name": nice_name(s.name), "sub": sub,
            "text": text, "override": override, "above_stop_pct": round(dist, 1) if dist is not None else 0,
            "caption": caption, "today": price if closes else None,
            "closes": closes, "stops": stops,
        })

    if analysis:
        headline = analysis.data["headline"]
        summary = analysis.data["summary"]
        summary_title = _("Market picture")
        summary_text = f"{headline} {summary}"
        watch = [{"title": it["headline"], "text": it["why_it_matters"], "next": it["watch_for"]}
                 for it in analysis.data.get("market_news", [])[:4]]
    else:
        summary_title = _("Rule results only")
        summary_text = _("AI analysis unavailable. The tables show the rule engine's results only.")
        watch = []
    if not watch:
        watch = [{"title": _("No market news in this run"), "text": "", "next": ""}]

    rows = [{"label": _("High risk"), "actual": port["strategy"]["actual_pct"].get("high_risk") or 0,
             "target": targets.get("high_risk")},
            {"label": _("Stocks"), "actual": port["strategy"]["actual_pct"].get("stocks") or 0,
             "target": targets.get("stocks")},
            {"label": _("Safe"), "actual": port["strategy"]["actual_pct"].get("safe") or 0,
             "target": targets.get("safe")},
            {"label": _("Unclassified"), "actual": port["strategy"].get("unclassified_pct") or 0, "target": None}]
    unknown = port.get("unknown_allocation", [])
    note = (_("Fund composition unavailable for: ") + ", ".join(nice_name(n) for n in unknown)) if unknown else ""
    iss = ctx.issue
    issue_line = (f"Börsensignale {iss['issue_key']}", f"{_('issue of')} {date_short(iss['issue_date'])}") if iss else \
        (_("no newsletter yet"), "")
    footer = _("Rule results come from the Börsensignale methods as implemented by the scripts; calls and "
               "confidence come from the AI analyst reading those facts. Confidence = strength of evidence, not a "
               "probability of gain. Decision support for your own account, not investment advice. Prices typically "
               "delayed ~15 min.")
    return {
        "lang": language,
        "labels": _labels(),
        "meta": {"eyebrow": _("Börsen-Agent · Daily brief"), "date": date_long(now), "time": f"{now:%H:%M}",
                 "issue": list(issue_line)},
        "kpis": kpis,
        "summary": {"title": summary_title, "text": summary_text, "chips": _chips(ms)},
        "actions_header": _("Weekly closes, last 12 months · dashed line = rolling 9-month stop"),
        "actions": actions,
        "watch": watch,
        "allocation": {"rows": rows, "legend": _("Strategy target · {n} positions in total").format(
            n=len(port["rows"]) - sum(1 for r in port["rows"] if r.get("cash"))), "note": note},
        "footer": footer,
    }


def render_pdf(data: dict, html_path: Path, pdf_path: Path) -> str:
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render(data), encoding="utf-8")
    return write_pdf(html_path, pdf_path)
