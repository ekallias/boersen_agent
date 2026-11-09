"""Inline SVG versions of the issue charts, in the daily brief's style. Vector graphics: sharp at any size.

votes_svg:   Gesamtsystem positive votes per issue (bars, labelled with the score)
zins_svg:    Zinsstruktur printed in the newsletter vs. computed by the agent (lines)
scatter_svg: the ~700-stock list, relative strength vs. KUV (log), with the highlighted groups
"""
from __future__ import annotations

import html
import math

from . import rules as R
from .i18n import _, num

PAPER, INK, MUTED, TRACK = "#F6F4EE", "#14171C", "#5B616B", "#E4E0D5"
NAVY, RED, GREEN, AMBER, ORANGE = "#1F3A5F", "#B3261E", "#1E6B4A", "#B97A00", "#C2622D"
DOT = "#B8B3A6"
GROUP_COLOURS = [NAVY, ORANGE, GREEN]


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def _svg(w: int, h: int, body: str, label: str) -> str:
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{_esc(label)}" '
            f'style="display:block">{body}</svg>')


def votes_svg(rows: list[dict]) -> str:
    data = [r for r in rows if r.get("score")]
    if len(data) < 2:
        return ""
    W, H, L, RR, T, B = 700, 180, 40, 14, 24, 56
    n = len(data)
    slot = (W - L - RR) / n
    top = 3.5

    def y(v: float) -> float:
        return T + (top - v) * (H - T - B) / top

    out = []
    for v in (0, 1, 2, 3):
        out.append(f'<line x1="{L}" x2="{W - RR}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{TRACK}"/>')
        out.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="15.0" fill="{MUTED}">{v}</text>')
    out.append(f'<line x1="{L}" x2="{W - RR}" y1="{y(1.5):.1f}" y2="{y(1.5):.1f}" stroke="{MUTED}" '
               f'stroke-dasharray="5 4"/>')
    out.append(f'<text x="{W - RR}" y="{y(1.5) - 4:.1f}" text-anchor="end" font-size="13.8" fill="{MUTED}">'
               f'{_esc(_("positive above"))}</text>')
    for k, r in enumerate(data):
        pos, neg = r["score"]
        x = L + k * slot + slot * 0.15
        w = slot * 0.7
        col = GREEN if pos > neg else RED
        out.append(f'<rect x="{x:.1f}" y="{y(pos):.1f}" width="{w:.1f}" height="{y(0) - y(pos):.1f}" '
                   f'fill="{col}" rx="2"/>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{y(pos) - 5:.1f}" text-anchor="middle" font-size="13.8" '
                   f'fill="{INK}">{pos}:{neg}</text>')
        d = R.d(r["date"]).strftime("%d.%m.")
        out.append(f'<text x="{x + w / 2:.1f}" y="{H - B + 6}" text-anchor="end" font-size="13.8" fill="{MUTED}" '
                   f'transform="rotate(-90 {x + w / 2:.1f} {H - B + 6})">{d}</text>')
    return _svg(W, H, "".join(out), _("Gesamtsystem per issue"))


def zins_svg(rows: list[tuple]) -> str:
    pub = [(R.d(d), p) for d, p, _c in rows if p is not None]
    comp = [(R.d(d), c) for d, _p, c in rows if c is not None]
    if len(pub) < 2 and len(comp) < 2:
        return ""
    values = [v for _d, v in pub + comp] + [0.0]
    lo, hi = min(values) - 0.05, max(values) + 0.12
    dates = sorted({d for d, _v in pub + comp})
    t0, t1 = dates[0], dates[-1]
    W, H, L, RR, T, B = 700, 210, 46, 130, 16, 38
    span_days = max((t1 - t0).days, 1)

    def x(d) -> float:
        return L + (d - t0).days / span_days * (W - L - RR)

    def y(v: float) -> float:
        return T + (hi - v) * (H - T - B) / (hi - lo)

    out = []
    step = 0.2
    v = math.floor(lo / step) * step
    while v <= hi:
        out.append(f'<line x1="{L}" x2="{W - RR}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{TRACK}"/>')
        out.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="13.8" fill="{MUTED}">'
                   f'{num(v, 1)}</text>')
        v += step
    out.append(f'<line x1="{L}" x2="{W - RR}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="{RED}" stroke-width="1.2"/>')
    out.append(f'<text x="{W - RR}" y="{y(0) + 14:.1f}" text-anchor="end" font-size="13.8" fill="{RED}">'
               f'{_esc(_("warning below 0"))}</text>')
    for k in (0, len(dates) // 2, len(dates) - 1):
        d = dates[k]
        out.append(f'<text x="{x(d):.1f}" y="{H - B + 18}" text-anchor="middle" font-size="13.8" fill="{MUTED}">'
                   f'{d.strftime("%b %y")}</text>')
    for series, col, label in ((pub, NAVY, _("newsletter")), (comp, ORANGE, _("computed"))):
        if len(series) < 2:
            continue
        pts = " ".join(f"{x(d):.1f},{y(val):.1f}" for d, val in series)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.4" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
        last_d, last_v = series[-1]
        out.append(f'<circle cx="{x(last_d):.1f}" cy="{y(last_v):.1f}" r="4" fill="{col}"/>')
        out.append(f'<text x="{x(last_d) + 8:.1f}" y="{y(last_v) + 4:.1f}" font-size="15.0" font-weight="600" '
                   f'fill="{col}">{_esc(label)} {num(last_v, 2)}</text>')
    return _svg(W, H, "".join(out), _("Zinsstruktur: printed in the newsletter vs. computed"))


def scatter_svg(universe: list[dict], highlights: dict[str, list[dict]], top_n: int, kuv_max: float) -> str:
    pts = [u for u in universe if u.get("rs") and u.get("kuv")]
    if len(pts) < 20:
        return ""
    W, H, L, RR, T, B = 700, 440, 56, 18, 16, 52
    xs = [u["rs"] for u in pts]
    xmin, xmax = max(0.0, min(xs) - 5), max(xs) + 5
    ymin, ymax = 0.03, max(u["kuv"] for u in pts) * 1.3
    lmin, lmax = math.log10(ymin), math.log10(ymax)

    def X(v: float) -> float:
        return L + (v - xmin) / (xmax - xmin) * (W - L - RR)

    def Y(v: float) -> float:
        return T + (lmax - math.log10(v)) * (H - T - B) / (lmax - lmin)

    out = []
    for dec in (0.1, 1, 10, 100):
        if ymin <= dec <= ymax:
            out.append(f'<line x1="{L}" x2="{W - RR}" y1="{Y(dec):.1f}" y2="{Y(dec):.1f}" stroke="{TRACK}"/>')
            out.append(f'<text x="{L - 8}" y="{Y(dec) + 4:.1f}" text-anchor="end" font-size="13.8" fill="{MUTED}">'
                       f'{num(dec, 1)}</text>')
    for xv in (100, 200, 300, 400):
        if xmin <= xv <= xmax:
            out.append(f'<line x1="{X(xv):.1f}" x2="{X(xv):.1f}" y1="{T}" y2="{H - B}" stroke="{TRACK}"/>')
            out.append(f'<text x="{X(xv):.1f}" y="{H - B + 16}" text-anchor="middle" font-size="13.8" '
                       f'fill="{MUTED}">{xv}</text>')
    ranked = sorted((u["rs"] for u in universe if u.get("rs")), reverse=True)
    if len(ranked) >= top_n:
        cut = ranked[top_n - 1]
        out.append(f'<line x1="{X(cut):.1f}" x2="{X(cut):.1f}" y1="{T}" y2="{H - B}" stroke="{MUTED}" '
                   f'stroke-dasharray="5 4"/>')
        out.append(f'<text x="{X(cut) + 4:.1f}" y="{T + 12}" font-size="13.8" fill="{MUTED}">'
                   f'{_esc(_("top {n} by RS").format(n=top_n))}</text>')
    if ymin <= kuv_max <= ymax:
        out.append(f'<line x1="{L}" x2="{W - RR}" y1="{Y(kuv_max):.1f}" y2="{Y(kuv_max):.1f}" stroke="{MUTED}" '
                   f'stroke-dasharray="5 4"/>')
        out.append(f'<text x="{W - RR}" y="{Y(kuv_max) - 5:.1f}" text-anchor="end" font-size="13.8" fill="{MUTED}">'
                   f'KUV {num(kuv_max, 0)}</text>')
    for u in pts:
        if xmin <= u["rs"] <= xmax:
            out.append(f'<circle cx="{X(u["rs"]):.1f}" cy="{Y(u["kuv"]):.1f}" r="2.4" fill="{DOT}"/>')
    labels = []
    legend = [(_("stock list"), DOT)]
    for (label, items), col in zip(highlights.items(), GROUP_COLOURS):
        items = [i for i in items if i.get("rs") and i.get("kuv")]
        if not items:
            continue
        legend.append((_(label), col))
        for i in items:
            out.append(f'<circle cx="{X(i["rs"]):.1f}" cy="{Y(i["kuv"]):.1f}" r="4.6" fill="{col}" '
                       f'stroke="{PAPER}" stroke-width="1.2"/>')
            labels.append(i)
    for i in sorted(labels, key=lambda i: -i["rs"])[:16]:
        text = i["name"] if len(i["name"]) <= 20 else i["name"][:19] + "…"
        out.append(f'<text x="{X(i["rs"]) + 7:.1f}" y="{Y(i["kuv"]) + 4:.1f}" font-size="13.8" fill="{INK}">'
                   f'{_esc(text)}</text>')
    lx, ly = W - RR - 200, T + 6
    out.append(f'<rect x="{lx - 8}" y="{ly - 4}" width="206" height="{18 * len(legend) + 8}" fill="{PAPER}" '
               f'stroke="{TRACK}"/>')
    for k, (label, col) in enumerate(legend):
        out.append(f'<circle cx="{lx}" cy="{ly + 14 + 18 * k - 4}" r="4" fill="{col}"/>')
        out.append(f'<text x="{lx + 10}" y="{ly + 14 + 18 * k}" font-size="13.8" fill="{INK}">{_esc(label)}</text>')
    out.append(f'<text x="{(L + W - RR) / 2}" y="{H - 10}" text-anchor="middle" font-size="13.8" fill="{MUTED}">'
               f'{_esc(_("relative strength (price ÷ 15-month median × 100)"))}</text>')
    out.append(f'<text x="14" y="{(T + H - B) / 2}" text-anchor="middle" font-size="13.8" fill="{MUTED}" '
               f'transform="rotate(-90 14 {(T + H - B) / 2})">{_esc(_("KUV (price / sales, log)"))}</text>')
    return _svg(W, H, "".join(out), _("Stock list: relative strength vs. KUV"))
