"""The closing commentary of a newsletter issue (its conclusion, right-hand column of page 5), taken
verbatim from the issue PDF for the issue report's first page."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader


def conclusion_text(newsletter_pdf: Path, page_number: int = 5) -> list[str]:
    """Lines of the closing commentary in the right-hand column of the given page.

    The commentary follows the Sicherheitssystem status line, the last status block of the right-hand column,
    and runs down to the footer. Raises ValueError if the layout is not recognised, so the caller can skip it.
    """
    page = PdfReader(str(newsletter_pdf)).pages[page_number - 1]
    width = float(page.mediabox.width)
    frags: list[tuple[float, float, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            frags.append((y, x, text))

    page.extract_text(visitor_text=visitor)
    footer_y = min((y for y, _x, t in frags if "boersensignale" in t.lower()), default=None)
    if footer_y is None:
        raise ValueError("footer not found on the newsletter page")
    lines: dict[float, list[tuple[float, str]]] = {}
    for y, x, text in frags:
        if x >= width * 0.5:
            lines.setdefault(round(y, 0), []).append((x, text))
    ordered = [(y, " ".join(t for _x, t in sorted(parts)).strip()) for y, parts in
               sorted(lines.items(), reverse=True)]
    ordered = [(y, t) for y, t in ordered if t and y > footer_y + 5]
    status = [i for i, (_y, t) in enumerate(ordered) if re.search(r"Sicherheitssys\s*tem", t, re.I)]
    if not status:
        raise ValueError("no Sicherheitssystem line in the right-hand column")
    body = [t for _y, t in ordered[status[-1] + 1:]]
    if len(body) < 2:
        raise ValueError("closing commentary too short")
    return body


def conclusion_block(newsletter_pdf: Path, page_number: int = 5) -> dict:
    """The conclusion as {heading, paragraph}. Only spacing is repaired; no words are changed."""
    lines = conclusion_text(newsletter_pdf, page_number)
    heading = lines[0] if len(lines[0]) < 90 and not lines[0].endswith(".") else None
    rest = lines[1:] if heading else lines
    text = re.sub(r"\s+", " ", " ".join(line.strip() for line in rest))
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"(\w) - ([a-zäöüß])", r"\1- \2", text)
    text = re.sub(r"(\w) - (\w)", r"\1-\2", text)
    return {"heading": heading, "paragraph": text, "page": page_number}
