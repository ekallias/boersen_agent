"""The biweekly newsletter report as HTML/PDF, in the layout of the Börsen-Agent weekly report template.

Content comes from the same context as the rest of the report (portfolio, newsletter picks, the AI
analysis, rule details). Charts are the PNGs made by `charts.py`, linked from the HTML. The PDF is
printed with headless Chrome (or WeasyPrint if installed), like the daily brief.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import svgcharts
from .brief import sparkline, write_pdf
from .i18n import _, action, date_long, date_short, num, reason
from .names import nice_name

PAPER, INK, MUTED, RULE, TRACK = "#F6F4EE", "#14171C", "#5B616B", "#CFCABB", "#E4E0D5"
BODY = "#2A2F37"
NAVY, RED, GREEN, AMBER = "#1F3A5F", "#B3261E", "#1E6B4A", "#B97A00"
SERIF = "Georgia, 'Fraunces', serif"
SANS = "'Inter', system-ui, sans-serif"

# tag colours per action: (background, text)
TAG = {
    "SELL": ("#F4DAD7", "#8C1A13"), "REDUCE": ("#F8E3CF", "#8A4B00"), "REVIEW": ("#F1E7C6", "#5E4A00"),
    "WATCH": ("#DDE7F5", "#1F3A5F"), "BUY": ("#DCEBE2", "#14502F"), "ADD": ("#DCEBE2", "#14502F"),
    "HOLD": ("#E8E4DA", "#4A4F57"),
}
VERDICT_COLOUR = {"confirmed": GREEN, "questionable": AMBER, "contradicted": RED}
REC_TAG = {"K": ("BUY", GREEN), "V": ("SELL", RED), "H": ("HOLD", MUTED)}  # code -> (action code, colour)
SIGN = {1: ("+", GREEN), -1: ("–", RED), 0: ("·", MUTED), None: ("?", MUTED)}
CHANGE_TAG = {"NEW": GREEN, "CALL": NAVY, "STOP": MUTED}


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _tag(code: str | None, conf: int | None = None) -> str:
    if not code:
        return "–"
    bg, fg = TAG.get(code.upper(), ("#E8E4DA", INK))
    suffix = f" <span class='conf'>{conf}</span>" if conf is not None else ""
    return f"<span class='tag' style='background:{bg};color:{fg}'>{esc(action(code).upper())}</span>{suffix}"


def _pct(v, nd=1) -> str:
    return "–" if v is None else num(v, nd, "%")


def _page(body: str, new_page: bool = False) -> str:
    cls = "page newpage" if new_page else "page"
    return f'<section class="{cls}">{body}</section>'


def _h2(text: str, cls: str = "") -> str:
    return f"<h2 class='{cls}'>{esc(text)}</h2>"


def _table(head: list[str], rows: list[list[str]], widths: list[int] | None = None) -> str:
    cols = "".join(f"<col style='width:{w}%'>" for w in widths) if widths else ""
    thead = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{('<colgroup>' + cols + '</colgroup>') if cols else ''}<thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"


def _img(path: Path | None, caption: str | None = None, note: str | None = None) -> str:
    if not path or not Path(path).exists():
        return ""
    cap = f"<h3>{esc(caption)}</h3>" if caption else ""
    nt = f"<p class='note'>{esc(note)}</p>" if note else ""
    return f"<div class='figure'>{cap}{nt}<img src='{Path(path).resolve().as_uri()}'></div>"


def _kpi(label: str, value: str, sub: str, tone: str | None) -> str:
    col = {"green": GREEN, "red": RED}.get(tone or "", INK)
    return (f"<div class='kpi'><div class='kl'>{esc(label)}</div>"
            f"<div class='kv' style='color:{col}'>{esc(value)}</div><div class='ks'>{esc(sub)}</div></div>")


def _change_row(text: str) -> tuple[str, str]:
    low = text.lower()
    if low.startswith("new") or low.startswith("removed"):
        return ("NEW" if low.startswith("new") else "OUT"), text
    if "stop" in low:
        return "STOP", text
    return "CALL", text


def render_issue(ctx, analysis, ai_error, now, changes: list[str], figs: dict, notes: list[str], disclaimer: str,
                 path: Path) -> Path:
    """Write the biweekly report as HTML and PDF (path is the PDF); returns the PDF path."""
    iss, dash = ctx.issue, ctx.dashboard or {}
    calls = analysis.calls_by_key() if analysis else {}
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    picks = [s for s in ctx.suggestions if s.newsletter_rec and not s.in_portfolio]
    cands = [s for s in ctx.suggestions if s.newsletter_rec is None and not s.in_portfolio]
    ms, port = ctx.market, ctx.portfolio
    g = dash.get("gesamt", {}).get("score")
    recs = [d["rec"] for d in ctx.issue_rows["dispo"]]
    depot = {}
    if iss.get("depot"):
        import json
        depot = json.loads(iss["depot"])
    d = analysis.data if analysis else None

    # --- header and KPIs
    head = (f"<div class='band'><div><div class='eyebrow'>{esc(_('Börsen-Agent · Weekly report'))}</div>"
            f"<h1>Börsensignale {esc(iss['issue_key'])}</h1>"
            f"<div class='sub'>{esc(_('Newsletter report · issue of {date}').format(date=date_short(iss['issue_date'])))}</div></div>"
            f"<div class='gen'>{esc(_('generated'))}<br><span class='genv'>{now:%d.%m.%Y %H:%M}</span></div></div>")
    kpis = ("<div class='kpis'>"
            + _kpi("Gesamtsystem", f"{g[0]}:{g[1]}" if g else "?",
                   _("positive" if g and g[0] > g[1] else "negative"),
                   ("green" if g and g[0] > g[1] else "red") if g else None)
            + _kpi(_("Model portfolio"), f"{depot.get('stocks_pct', '?')}%",
                   _("stocks · {cash}% cash").format(cash=depot.get("cash_pct", "?")), None)
            + _kpi(_("Buy"), str(recs.count("K")), _("K in the Dispo-Liste"), "green")
            + _kpi(_("Sell"), str(recs.count("V")), _("V in the Dispo-Liste"), "red" if recs.count("V") else None)
            + _kpi(_("New ideas"), str(len(d["new_ideas"]) if d else len(cands)), _("from the 700-stock list"), None)
            + "</div>")
    page1 = [head, kpis]

    # --- lead and market news
    if d:
        page1.append(f"<div class='lead'>{esc(d['headline'])}</div><p class='body'>{esc(d['summary'])}</p>")
        summary = d.get("newsletter_summary") or []
        if summary:
            page1.append(_h2(_("Newsletter summary")) + "<ul class='summary'>" + "".join(
                f"<li>{esc(item)}</li>" for item in summary) + "</ul>")
        mv = d["market_view"]
        page1.append(f"<div class='view'><b>{esc(_('Market view:'))}</b> {esc(_(mv['stance'].replace('_', '-')))} "
                     f"({esc(_('confidence'))} {mv['confidence']}) — {esc(mv['reasoning'])}</div>")
        if d.get("market_news"):
            items = "".join(f"<div class='news'><div class='nt'>{esc(n['headline'])}</div>"
                            f"<div class='nb'>{esc(n['why_it_matters'])}</div>"
                            f"<div class='nw'>→ {esc(n['watch_for'])}</div></div>" for n in d["market_news"])
            page1.append(_h2(_("Market news: what to watch")) + items)
    else:
        page1.append(f"<div class='view warn'>{esc(_('AI analysis unavailable: {err}. The tables show the rule engine\'s results only.').format(err=ai_error or 'disabled in config'))}</div>")
    body1 = "".join(page1)

    # --- your portfolio against this issue
    rows = []
    for s in mine:
        c = calls.get(s.key)
        ai = _tag(c["action"], c["confidence"]) if c else "–"
        why = esc(c["rationale"]) if c else esc(reason(s.reasons[0]["text"]) if s.reasons else "")
        if c and c.get("overrides_9m_sell"):
            why = f"<span class='warnline'>⚠ {esc(_('overrides the 9-month-low sell rule:'))}</span> {esc(c.get('override_reason') or '')}<br>" + why
        elif c and c.get("overrides_stated_rule"):
            why = f"<span class='warnline'>⚠ {esc(_('differs from the rule:'))}</span> {esc(c.get('override_reason') or '')}<br>" + why
        rows.append([f"<b>{esc(nice_name(s.name))}</b>", ai, _tag(s.action, s.confidence), "–", why])
    portfolio_block = _h2(_("Your portfolio against this issue")) + (
        _table([_("Security"), _("AI call"), _("Rules"), _("Newsletter"), _("Rationale")], rows, [22, 14, 14, 12, 38])
        if rows else f"<p class='body'>{esc(_('No portfolio imported yet.'))}</p>")

    # --- newsletter picks checked
    rows = []
    for s in sorted(picks, key=lambda x: (x.newsletter_rec != "V", x.name)):
        c = calls.get(s.key)
        rec_code, rc = REC_TAG.get(s.newsletter_rec, (None, MUTED))
        rec = action(rec_code) if rec_code else "–"
        verdict = (c or {}).get("verdict")
        vcol = VERDICT_COLOUR.get(verdict, MUTED)
        check = _("AI {verdict}").format(verdict=_(verdict)) if verdict else _(s.verification or "")
        why = esc(c["rationale"]) if c else esc(reason(s.reasons[0]["text"]) if s.reasons else "")
        if c and c.get("overrides_stated_rule"):
            why = f"<span class='warnline'>⚠ {esc(_('differs from the rule:'))}</span> {esc(c.get('override_reason') or '')}<br>" + why
        rows.append([f"<b>{esc(nice_name(s.name))}</b>", _tag(c["action"], c["confidence"]) if c else "–",
                     _tag(s.action, s.confidence),
                     f"<span style='color:{rc};font-weight:600'>{esc(rec)}</span><br><span style='color:{vcol}'>{esc(check)}</span>",
                     why])
    picks_block = _h2(_("Newsletter recommendations, checked")) + f"<p class='note'>{esc(_('Newsletter = the issue\'s buy / hold / sell, with the AI analyst\'s verdict (confirmed · questionable · contradicted), or the scripts\' live check when there is no AI analysis.'))}</p>" + (
        _table([_("Security"), _("AI call"), _("Rules"), _("Newsletter"), _("Rationale")], rows, [20, 13, 13, 16, 38])
        if rows else f"<p class='body'>{esc(_('All of this issue\'s recommendations are in your portfolio (see above).'))}</p>")

    # --- new ideas
    rows = []
    ideas = {c["key"]: c for c in (d["new_ideas"] if d else [])}
    for s in cands:
        if d and s.key not in ideas:
            continue
        c = ideas.get(s.key)
        ai = _tag(c["action"], c["confidence"]) if c else "–"
        why = esc(c["rationale"]) if c else esc(reason(s.reasons[0]["text"]) if s.reasons else "")
        if c and c.get("overrides_stated_rule"):
            why = f"<span class='warnline'>⚠ {esc(_('differs from the rule:'))}</span> {esc(c.get('override_reason') or _('(no reason given)'))}<br>" + why
        rows.append([f"<b>{esc(nice_name(s.name))}</b>", ai, _tag(s.action, s.confidence), why])
    ideas_block = (_h2(_("New ideas from the 700-stock list")) + _table(
        [_("Security"), _("AI call"), _("Rules"), _("Rationale")], rows, [24, 14, 14, 48])) if rows else ""

    # --- risks and questions
    rq = ""
    if d:
        rq = ("<div class='two'><div>" + _h2(_("Risks")) + "<ul>" + "".join(f"<li>{esc(r)}</li>" for r in d["risks"])
              + "</ul></div><div>" + _h2(_("Questions for you")) + "<ul>"
              + "".join(f"<li>{esc(q)}</li>" for q in d["questions_for_user"]) + "</ul></div></div>")

    # --- changes since the previous issue
    ch_items = [_change_row(reason(c)) for c in changes] or [("NEW", _("first ingested issue"))]
    half = (len(ch_items) + 1) // 2
    def col(items):
        return "".join(f"<div class='chg'><span class='ct' style='color:{CHANGE_TAG.get(k, MUTED)}'>{esc(k)}</span>{esc(t)}</div>"
                       for k, t in items)
    changes_block = _h2(_("Changes since the previous issue")) + f"<div class='two'><div>{col(ch_items[:half])}</div><div>{col(ch_items[half:])}</div></div>"

    p1 = _page(body1)
    stock_series = figs.get("stock_series") or []
    buy_block = _buy_sections(stock_series, (d or {}).get("stock_profiles") or {})
    p2 = _page(portfolio_block + picks_block)
    idea_block = _buy_sections(figs.get("idea_series") or [], (d or {}).get("stock_profiles") or {},
                               heading=_("New ideas in detail"), action_code="WATCH")
    p3 = _page(buy_block + ideas_block + idea_block + rq + changes_block)

    # --- charts
    stock_series = figs.get("stock_series") or []
    chart_parts = [_h2(_("Charts"))]
    chart_parts.append(_svg_figure(svgcharts.votes_svg(figs.get("gesamt_rows") or []), _("Gesamtsystem per issue")))
    zins = figs.get("zins_history")
    chart_parts.append(_svg_figure(svgcharts.zins_svg(figs.get("zins_rows") or []),
                                   _("Zinsstruktur: printed in the newsletter vs. computed"),
                                   _("The rule only uses the sign (warning below 0); the computed series runs about 0.1 higher.")))
    sc = figs.get("scatter")
    chart_parts.append(_svg_figure(svgcharts.scatter_svg(**sc) if sc else "", _("Stock list: relative strength vs. KUV"),
                                   _("Buy candidates sit right of the top-200 line and below the KUV line.")))
    chart_parts.append(_alloc_bars(ctx))
    p4 = _page("".join(chart_parts), new_page=True)
    p5 = _page(_h2(_("Price charts: stocks labelled SELL, REDUCE, REVIEW or BUY"))
               + "<p class='note'>" + esc(_("Weekly closes over the last year. The dashed line is the rolling 9-month stop (39 weeks).")) + "</p>"
               + _price_cards(stock_series))

    # --- regime and portfolio
    reg_rows = []
    for c in ms.components.values():
        now_s, now_c = SIGN.get(c.state, ("?", MUTED))
        new_s, new_c = SIGN.get(c.newsletter, ("?", MUTED))
        warn = " ⚠" if c.diverges else ""
        reg_rows.append([esc(_(c.label)), f"<span style='color:{now_c};font-weight:700'>{now_s}</span>{warn}",
                         f"<span style='color:{new_c};font-weight:700'>{new_s}</span>", esc(_(c.source)), esc(c.marker),
                         esc(reason(c.detail))])
    regime = (_h2(_("Market regime, re-checked with live data"))
              + _table([_("Component"), _("Now"), _("Issue"), _("Source"), _("Rule"), _("Detail")], reg_rows,
                       [22, 9, 9, 13, 8, 39]))
    prow = []
    for r in port["rows"]:
        if r.get("cash"):
            prow.append([esc(nice_name(r["name"])), esc(_("cash")), "–", "–", num(r["value_eur"], 2), "–"])
            continue
        q = r["quantity"]
        pnl = r["pnl_pct"]
        pnl_cell = "–" if pnl is None else f"<span style='color:{GREEN if pnl >= 0 else RED}'>{num(pnl, 1, '%')}</span>"
        prow.append([esc(nice_name(r["name"])), esc(_(r["asset"])), num(q, 0 if q == int(q) else 2),
                     f"{num(r['price'], 2)} {esc(r['currency'] or '')}", num(r["value_eur"], 2), pnl_cell])
    portfolio_tbl = (_h2(_("Portfolio")) + _table([_("Position"), _("Type"), _("Qty"), _("Price"), _("Value €"), _("P/L")],
                                                  prow, [34, 12, 12, 16, 16, 10]))
    notes_html = "".join(f"<p class='note'>{esc(n)}</p>" for n in (port.get("notes") or []))
    p6 = _page(regime + portfolio_tbl + notes_html)

    # --- appendix and data notes
    appendix_parts = [_h2(_("Appendix: rule details per security"))]
    for s in mine + picks + cands:
        c = calls.get(s.key)
        call = c["action"] if c else s.action
        bullets = "".join(f"<li>{esc(reason(r['text']))}{(' [' + esc(r['marker']) + ']') if r.get('marker') else ''}</li>"
                          for r in s.reasons)
        appendix_parts.append(f"<div class='appx'><div class='ah'><b>{esc(nice_name(s.name))}</b> {_tag(call)} "
                              f"<span class='sym'>{esc(s.symbol or '')}</span></div><ul>{bullets}</ul></div>")
    notes_all = list(dict.fromkeys(notes))
    if notes_all:
        appendix_parts.append(_h2(_("Data notes")) + "".join(f"<p class='note'>• {esc(reason(n))}</p>" for n in notes_all))
    if analysis is not None:
        u = analysis.usage or {}
        appendix_parts.append(f"<p class='note'>AI: {esc(analysis.model)} · tokens in {u.get('input_tokens')} "
                              f"(cached {u.get('cache_read_input_tokens')}) / out {u.get('output_tokens')}"
                              + (f" · guardrail notes: {esc('; '.join(analysis.warnings))}" if analysis.warnings else "")
                              + "</p>")
    appendix_parts.append(f"<p class='disc'>{esc(_(disclaimer))}</p>")
    p7 = _page("".join(appendix_parts), new_page=True)

    doc = _document(f"Börsensignale {iss['issue_key']}", "".join([p1, p2, p3, p4, p5, p6, p7]))
    html_path = path.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(doc, encoding="utf-8")
    write_pdf(html_path, path)
    return path


ACTION_COLOUR = {"SELL": RED, "REDUCE": RED, "REVIEW": AMBER, "WATCH": NAVY, "BUY": GREEN, "ADD": GREEN,
                 "HOLD": MUTED}


def _buy_sections(series: list[dict], profiles: dict, heading: str | None = None, action_code: str = "BUY") -> str:
    """One section per stock with the given AI call (BUY for purchases, WATCH for new ideas): chart, profile,
    and fit in the strategy."""
    items = [s for s in series if (s.get("action") or "").upper() == action_code]
    if not items:
        return ""
    out = [_h2(heading or _("Purchase recommendations in detail"))]
    for s in items:
        profile = (profiles.get(s.get("key")) or {}).get("profile", "")
        if s.get("closes"):
            svg = sparkline(s["closes"], s["stops"], s.get("today"), colour=GREEN, w=700, h=230, pad=10,
                            label=s["name"])
        else:
            svg = f"<p class='note'>{esc(_('No price chart: no market symbol'))}</p>"
        fit = []
        share, target = s.get("stock_share"), s.get("stock_target")
        if share is not None and target is not None:
            fit.append(_("Stock share today {a} % against a target of {t} %").format(a=num(share, 0), t=num(target, 0)))
            fit.append(_("A purchase would raise the stock share further above target; the strategy argues against more stocks while it is above target.")
                       if share > target else
                       _("There is room in the stock target; a purchase fits the target allocation."))
        fit.append(_("Included in the BS Best fund (estimated from the Dispo-Liste)") if s.get("in_fund")
                   else _("Not part of the BS Best fund (no Dispo recommendation)"))
        fit.append(_("Rules: {rule} ({rc}); AI call: {ai}").format(
            rule=action(s.get("rule_action")), rc=s.get("rule_confidence"), ai=action(s["action"])))
        numbers = " · ".join(x for x in (
            f"RS {num(s['rs'], 0)}" if s.get("rs") is not None else "",
            f"KUV {num(s['kuv'], 2)}" if s.get("kuv") is not None else "",
            f"KBV {num(s['kbv'], 2)}" if s.get("kbv") is not None else "") if x)
        out.append(
            f"<div class='buy'><div class='ph'><span class='pn big'>{esc(s['name'])}</span>{_tag(s['action'])}</div>"
            f"{svg}<div class='pc'>{esc(s.get('caption') or '')}</div>"
            f"<h3>{esc(_('Profile'))}</h3><p class='body'>{esc(profile or _('No profile yet'))}</p>"
            f"<h3>{esc(_('Fit in the investment strategy'))}</h3><ul>"
            + "".join(f"<li>{esc(x)}</li>" for x in fit) + "</ul>"
            + (f"<p class='note'>{esc(numbers)}</p>" if numbers else "") + "</div>")
    return "".join(out)


def _svg_figure(svg: str, caption: str, note: str | None = None) -> str:
    if not svg:
        return ""
    nt = f"<p class='note'>{esc(note)}</p>" if note else ""
    return f"<div class='figure'><h3>{esc(caption)}</h3>{nt}{svg}</div>"


def _price_cards(series: list[dict]) -> str:
    """Price cards in the daily brief's style: weekly closes, dashed 9-month stop, live price ring."""
    if not series:
        return ""
    cards = []
    for s in series:
        colour = ACTION_COLOUR.get((s.get("action") or "").upper(), NAVY)
        svg = sparkline(s["closes"], s["stops"], s.get("today"), colour=colour, w=330, h=120, pad=7, label=s["name"])
        cards.append(f"<div class='pcard'><div class='ph'><span class='pn'>{esc(s['name'])}</span>"
                     f"{_tag(s.get('action'))}</div>{svg}<div class='pc'>{esc(s.get('caption') or '')}</div></div>")
    return "<div class='pgrid'>" + "".join(cards) + "</div>"


def _alloc_bars(ctx) -> str:
    """Holdings by value, as horizontal bars in the daily brief's style."""
    rows = sorted([r for r in ctx.portfolio["rows"] if r.get("value_eur")], key=lambda r: -r["value_eur"])
    if not rows:
        return ""
    top = rows[0]["value_eur"]
    colour = {"stock": NAVY, "fund": "#2F7A6E", "bond": "#8A8F98"}
    out = []
    for r in rows:
        asset = "bond" if r.get("cash") else (r.get("asset") or "stock")
        width = r["value_eur"] / top * 100
        out.append(f"<div class='brow'><div class='bl'>{esc(nice_name(r['name']))}</div>"
                   f"<div class='bb'><div style='width:{width:.1f}%;background:{colour.get(asset, NAVY)}'></div></div>"
                   f"<div class='bv'>{num(r['value_eur'], 0)} €</div></div>")
    return _h2(_("Your allocation")) + "<div class='bars'>" + "".join(out) + "</div>"


def _document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
@page {{ size: A4; margin: 0 }}
html, body {{ margin: 0; background: {PAPER}; color: {INK}; font-family: {SANS}; font-size: 13.4px; line-height: 1.45;
  -webkit-print-color-adjust: exact; print-color-adjust: exact }}
.page {{ box-sizing: border-box; width: 210mm; padding: 26mm 14mm 14mm; position: relative }}
.newpage {{ page-break-before: always }}
h2, h3 {{ break-after: avoid }}
.figure, .pcard, .appx, .news, .chg, .view, .buy {{ break-inside: avoid }}
.buy {{ margin-bottom: 14px }} .pn.big {{ font-size: 15px }} .buy ul {{ font-size: 11px }}
.kpis, .lead {{ break-after: avoid }}
h1 {{ margin: 2px 0 4px; font-family: {SERIF}; font-weight: 500; font-size: 38.4px; line-height: 1.1 }}
h2 {{ margin: 14px 0 6px; font-family: {SERIF}; font-weight: 500; font-size: 21.8px; border-bottom: 1.5px solid {INK}; padding-bottom: 4px }}
h2.first {{ margin-top: 0 }}
h3 {{ margin: 8px 0 2px; font-family: {SERIF}; font-weight: 500; font-size: 16.6px }}
.band {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1.5px solid {INK}; padding-bottom: 8px }}
.eyebrow {{ font-size: 10.9px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: {MUTED} }}
.sub {{ color: {MUTED}; font-size: 14.1px }}
.gen {{ text-align: right; font-size: 11.5px; color: {MUTED} }}
.genv {{ font-family: {SERIF}; font-size: 16.6px; color: {INK} }}
.kpis {{ display: grid; grid-template-columns: repeat(5, 1fr); border-bottom: 1px solid {RULE}; padding: 10px 0 12px }}
.kpi {{ padding: 0 10px; border-right: 1px solid {RULE} }}
.kpi:first-child {{ padding-left: 0 }} .kpi:last-child {{ border-right: 0 }}
.kl {{ font-size: 10.2px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: {MUTED} }}
.kv {{ font-family: {SERIF}; font-size: 30.7px; line-height: 1.1; margin: 2px 0 1px }}
.ks {{ font-size: 11.5px; color: {MUTED} }}
.lead {{ font-family: {SERIF}; font-size: 21.8px; line-height: 1.3; margin: 12px 0 6px }}
.body {{ margin: 0 0 6px; font-size: 14.1px; color: {BODY} }}
.view {{ background: #ECE8DC; padding: 8px 10px; margin: 8px 0; font-size: 12.8px }}
.warn {{ color: {RED} }}
.news {{ border-bottom: 1px solid {RULE}; padding: 6px 0 }}
.nt {{ font-weight: 600 }} .nb {{ color: {BODY} }} .nw {{ color: {NAVY}; font-weight: 500 }}
table {{ width: 100%; border-collapse: collapse; font-size: 12.2px; margin-bottom: 4px }}
th {{ text-align: left; font-size: 9.6px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: {MUTED};
  border-bottom: 1px solid {INK}; padding: 4px 4px 4px 0 }}
td {{ border-bottom: 1px solid {RULE}; padding: 5px 4px 5px 0; vertical-align: top }}
tr {{ page-break-inside: avoid }}
.tag {{ display: inline-block; padding: 2px 6px; font-size: 10.2px; font-weight: 600; letter-spacing: .08em }}
.conf {{ color: {MUTED}; font-size: 10.2px }}
.warnline {{ color: #B0560A; font-weight: 600 }}
.note {{ font-size: 10.9px; color: {MUTED}; margin: 3px 0 6px }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px }}
ul {{ margin: 2px 0 6px; padding-left: 14px }} li {{ margin: 0 0 3px }}
.chg {{ border-bottom: 1px solid {RULE}; padding: 3px 0; font-size: 12.2px }}
.summary li {{ margin-bottom: 4px }}
.ct {{ display: inline-block; width: 42px; font-size: 9.6px; font-weight: 600; letter-spacing: .08em }}
.figure {{ margin: 6px 0 10px }} .figure img {{ width: 100%; height: auto }}
.pgrid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px }}
.pcard {{ border-bottom: 1px solid {RULE}; padding-bottom: 6px; break-inside: avoid }}
.ph {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px }}
.pn {{ font-weight: 600; font-size: 12.8px }} .pc {{ font-size: 10.9px; color: {MUTED} }}
.bars {{ margin-top: 4px }} .brow {{ display: grid; grid-template-columns: 36% 1fr 20%; gap: 8px; align-items: center; margin: 5px 0; font-size: 12.2px }}
.bb {{ height: 9px; background: {TRACK}; border-radius: 4px; overflow: hidden }} .bb div {{ height: 9px }}
.bv {{ text-align: right; color: {MUTED} }}
.appx {{ break-inside: avoid; margin-bottom: 7px; border-bottom: 1px solid {RULE}; padding-bottom: 4px }}
.ah {{ margin-bottom: 2px }} .sym {{ color: {MUTED}; font-size: 10.9px; margin-left: 4px }}
.appx ul {{ font-size: 10.9px }}
.disc {{ font-size: 10.9px; color: {MUTED}; margin-top: 12px }}
</style></head><body>{body}</body></html>"""
