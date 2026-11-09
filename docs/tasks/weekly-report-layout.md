# Task: fix the layout of the weekly (issue) report

Owner: one session, Sonnet 5.5 is enough. Scope is layout and presentation only.

## Goal

The issue report (`reports/<issue>_newsletter-report.pdf`, rendered by `boersen_agent/weekly.py`) should be
clean on A4 pages: no overflow between pages, no clipped labels, consistent spacing, and sections in a
sensible reading order. Content and rules stay as they are.

## Context

- Rendering: `weekly.render_issue` builds HTML (`_document`, `_page`, `_h2`, `_table` helpers, CSS in
  `_document`) and `brief.write_pdf` prints it with headless Chrome. Each page is a `.page` div of A4 height.
- Charts: `boersen_agent/pricechart.py` (`price_svg`) draws the price cards with a y-scale, high and low lines
  with values, date labels, and on big cards (w >= 600) 10-week and 39-week moving averages.
  Price cards are used in `weekly._buy_sections` (700 px wide) and `weekly._price_cards` (330 px wide).
- Page 1 holds: header and KPIs, headline and summary, newsletter summary (bullets), market view, market news,
  and the "Fazit des Newsletters" block at the bottom. It is nearly full. The Fazit paragraph is set at 11.5 px
  to fit; the Fazit must stay on page 1.
- Page 2: "Ihr Depot im Lichte dieser Ausgabe" (portfolio against this issue), newsletter picks, new ideas.
- Later pages: buy and idea sections with price cards and profiles, strategy allocation and diversified funds
  table (`weekly._strategy_block`), market regime table, portfolio table, appendix.
- Recent layout issues to check first:
  1. Page 1 is at its limit: any added line pushes text onto page 2.
  2. The price cards were just changed (axes, H/L labels, averages). Check the right-hand H/L labels are not
     clipped, the date labels do not collide, and the moving-average legend does not sit on the price line.
  3. The strategy section and the fund table come before the regime and portfolio tables on the same page; the
     table widths may not fit.
  4. Empty space at the end of some pages and cramped text on others.

## Constraints

- Do not change rules, analysis content, the JSON schema, or what is shown. Layout and styling only.
- Do not change `analyst.py` or `docs/METHODS.md`.
- `boersen_agent/agent.py`, `config.py` and `chart_data.py` are being edited by another planning session
  (chunk A in `docs/web-plan.md`). Keep changes to `weekly.py`, `pricechart.py` and `i18n.py` where possible.
  If you must touch `agent.py`, make one small edit and re-read the file first.
- Do not send e-mail while testing. Render with `finish` from a copy of the hand-off folder, or call
  `weekly.render_issue` directly. The mailer is only used by `Agent._publish`.
- Do not use the Anthropic API for testing. Use an existing analysis file (`data/handoff/issue-2026-23_analysis.json`
  and the facts beside it), or pass `analysis=None` to get the rule-based report.
- Work on a branch, not the default branch. Commit with the attribution line from the repo instructions.
  Do not include model identifiers in commit messages or PR text.

## How to test

1. Render the report. Example: copy the hand-off folder, write a manifest with the issue task only, and run
   `python -m boersen_agent --config config.toml finish --out <copy> --model claude-haiku-5-5` with
   `ANTHROPIC_API_KEY` unset. Or run a short script that calls `weekly.render_issue` with stored data.
2. Check the page count and the page breaks: `pdfinfo` and `pdftotext -f N -l N` for each page. The first
   line of each page should be a new section, not a continuation sentence.
3. Look at every page: `pdftoppm -r 60 -png <pdf>` and view the images at least for pages 1, 2, the first
   buy-section page, the strategy page and the last page.
4. Run tests: `python -m unittest discover -s tests -t .`. Known failures that are not yours:
   `test_german_pdfs_markdown_and_prompt` (heading mismatch, pre-existing) and `test_chart_data` (in progress
   in chunk A).

## Acceptance

- Each page fits its A4 frame. No section continues onto a new page without a heading.
- Page 1 ends with the Fazit block, in full.
- Price cards: y-scale, H and L labels with values, and date labels are all visible and not clipped.
  Averages appear only on the large cards, and their legend does not cover the price line.
- Tables fit the page width. Column headers are readable.
- Same report content as before (compare section headings and figures with the current PDF).
- Tests as above, with only the known failures.
- A short summary is appended to the "Log" section below, with changed files, test result and open items.

## Log

- 2026-11-09: task written. Not started.
