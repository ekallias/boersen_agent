"""Price charts for the reports (SVG): y scale, high and low lines with values, date labels, and on the
larger charts 10-week and 39-week moving averages of the weekly closes."""
from __future__ import annotations

import math
import re

from .i18n import num

PAPER, MUTED, RULE = "#F6F4EE", "#5B616B", "#CFCABB"
NAVY, AMBER, TEAL, STOP_GREY, INK = "#1F3A5F", "#B97A00", "#2F7A6E", "#8A8F98", "#14171C"
MA_SHORT, MA_LONG = 10, 39   # weeks: about 2.5 months (short trend) and 9 months (the rolling-stop horizon)
SANS = "Inter, system-ui, sans-serif"


def window_averages(full_closes: list[float], window_len: int) -> dict[str, list[float | None]]:
    """Moving averages of the weekly closes, aligned to the last `window_len` closes.

    The averages use the full history, so the first points of the window are not distorted."""
    start = max(0, len(full_closes) - window_len)
    out: dict[str, list[float | None]] = {}
    for win in (MA_SHORT, MA_LONG):
        out[f"ma{win}"] = [sum(full_closes[i - win + 1:i + 1]) / win if i >= win - 1 else None
                           for i in range(start, len(full_closes))]
    return out


def nice_ticks(lo: float, hi: float, count: int = 3) -> list[float]:
    """About `count` round tick values covering [lo, hi]."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(count - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    ticks, v = [], math.ceil(lo / step) * step
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _fmt(v: float) -> str:
    return num(v, 0 if abs(v) >= 1000 else 2)


def _mmyy(date_str) -> str:
    m = re.match(r"^\d{2}(\d{2})-(\d{2})", str(date_str or ""))
    return f"{m.group(2)}/{m.group(1)}" if m else ""


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def price_svg(closes, stops, *, dates=None, today=None, colour=NAVY, w=230, h=54, label="",
              averages=None, big=None) -> str:
    """Inline SVG price chart: closes (solid), rolling stop (dashed grey), optional live price (ring),
    scale with ticks, high and low lines with values, and on big charts the moving averages."""
    n = len(closes)
    if n < 2:
        return ""
    big = (w >= 600) if big is None else big
    left, right = (46, 66) if big else (34, 48)
    top, bottom = (22, 22) if big else (6, 15)
    ahead = 2 if today is not None else 0
    avgs = {k: v for k, v in (averages or {}).items() if big and k in ("ma10", "ma39") and len(v) == n}
    values = list(closes) + list(stops) + ([today] if today is not None else [])
    for series in avgs.values():
        values += [v for v in series if v is not None]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pw, ph = w - left - right, h - top - bottom

    def x(i: float) -> float:
        return left + i * pw / (n - 1 + ahead)

    def y(v: float) -> float:
        return top + (hi - v) * ph / span

    def segments(vals) -> list[str]:
        out, cur = [], []
        for i, v in enumerate(vals):
            if v is None:
                if cur:
                    out.append(" ".join(cur))
                cur = []
            else:
                cur.append(f"{x(i):.1f},{y(v):.1f}")
        if cur:
            out.append(" ".join(cur))
        return out

    fs = 9 if big else 8
    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{_esc(label)}" '
             f'style="display: block; overflow: visible; font-family: {SANS}">']
    # y scale
    for t in nice_ticks(lo, hi):
        if lo - span * 1e-6 <= t <= hi + span * 1e-6:
            parts.append(f'<line x1="{left}" y1="{y(t):.1f}" x2="{left + pw}" y2="{y(t):.1f}" '
                         f'stroke="{RULE}" stroke-width="0.5"/>')
            parts.append(f'<text x="{left - 4}" y="{y(t) + 3:.1f}" text-anchor="end" font-size="{fs}" '
                         f'fill="{MUTED}">{_esc(_fmt(t))}</text>')
    # high and low of the displayed window
    hi_c, lo_c = max(closes), min(closes)
    for v, tag in ((hi_c, "H"), (lo_c, "L")):
        parts.append(f'<line x1="{left}" y1="{y(v):.1f}" x2="{left + pw}" y2="{y(v):.1f}" stroke="{MUTED}" '
                     f'stroke-width="0.8" stroke-dasharray="1 3"/>')
        parts.append(f'<text x="{left + pw + 4}" y="{y(v) + 3:.1f}" font-size="{fs}" fill="{INK}" '
                     f'font-weight="600">{tag} {_esc(_fmt(v))}</text>')
    # moving averages (big charts only)
    for key, colour_ma in (("ma10", AMBER), ("ma39", TEAL)):
        for seg in segments(avgs.get(key, [])):
            parts.append(f'<polyline points="{seg}" fill="none" stroke="{colour_ma}" stroke-width="1.1" '
                         f'stroke-linejoin="round" opacity="0.9"/>')
    # stop, price, live price
    for seg in segments(stops):
        parts.append(f'<polyline points="{seg}" fill="none" stroke="{STOP_GREY}" stroke-width="1.2" '
                     f'stroke-dasharray="3 3" stroke-linejoin="round"/>')
    for seg in segments(closes):
        parts.append(f'<polyline points="{seg}" fill="none" stroke="{NAVY}" stroke-width="1.8" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
    last_x, last_y = x(n - 1), y(closes[-1])
    if today is not None:
        tx, ty = x(n - 1 + ahead), y(today)
        parts.append(f'<line x1="{last_x:.1f}" y1="{last_y:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" stroke="{colour}" '
                     f'stroke-width="1.3" stroke-dasharray="2 2"/>')
        parts.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3.4" fill="{PAPER}" stroke="{colour}" '
                     f'stroke-width="1.8"/>')
    parts.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="{colour}"/>')
    # date labels
    if dates and len(dates) == n:
        idx = sorted({0, round((n - 1) / 3), round(2 * (n - 1) / 3), n - 1}) if big else [0, n - 1]
        for i in idx:
            text = _mmyy(dates[i])
            if not text:
                continue
            anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
            parts.append(f'<text x="{x(i):.1f}" y="{h - 4}" text-anchor="{anchor}" font-size="{fs}" '
                         f'fill="{MUTED}">{_esc(text)}</text>')
    # legend for the averages
    if avgs:
        parts.append(f'<text x="{left + 6}" y="{top - 11}" font-size="{fs}" fill="{AMBER}">'
                     f'— {MA_SHORT} W avg</text>')
        parts.append(f'<text x="{left + 80}" y="{top - 11}" font-size="{fs}" fill="{TEAL}">'
                     f'— {MA_LONG} W avg</text>')
    parts.append("</svg>")
    return "".join(parts)
