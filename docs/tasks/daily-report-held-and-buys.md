# Feature: daily report shows only actionable recommendations

Owner: one session, Sonnet 5.5. Branch `feature/daily-report-held-and-buys`.

## Problem

The daily brief lists recommendations for stocks the investor does not hold, which come from the
newsletter's fund (BS Best) and the newsletter's picks. The investor cannot buy or sell these directly, so they
add noise. Only actions the investor can take should appear.

## Required behaviour

The daily brief ("Heute" / Today section, the action rows and the chips that refer to them) shows only:

1. Recommendations for positions the investor holds (SELL, REDUCE, REVIEW, WATCH, ADD, HOLD where a
   change is needed as today), and
2. BUY or ADD recommendations for other securities (new buy candidates or newsletter buy picks).

It does not show SELL, REDUCE, REVIEW, WATCH or HOLD for securities that are not held. Those stay in the
issue report (the newsletter report), where they belong.

## Where to look

- `boersen_agent/report.py: daily_action_items` filters suggestions by `in_portfolio` for the action list.
  Check whether non-held items reach the daily brief through another path: the AI daily analysis
  (`position_calls`, `newsletter_picks`, `new_ideas`, `market_news`), the markdown in `report.daily`, or the
  `brief.daily_data` / `brief.py` watch and action sections.
- `boersen_agent/analyst.py: validate` keeps only keys that are in the fact pack; check which keys the daily
  fact pack contains.
- `boersen_agent/agent.py: daily` / `Agent.daily` build the daily data.

Reproduce the problem first with the latest daily facts (`data/handoff/daily_facts.json`) and the daily
report output, and write down exactly which path puts non-held items into the daily brief. Put the finding in
the Log before changing code.

## Constraints

- Change presentation and selection only. Do not change rule logic, confidence scoring, the schema of the
  analysis, or the issue report.
- Do not change how holdings are determined (`in_portfolio`).
- The issue report keeps all recommendations.
- Do not send e-mail while testing. Do not use the Anthropic API while testing; use the existing analysis
  file or the rule-only path.
- Do not edit `agent.py` in the main checkout (another session works there). This worktree is separate.

## Test plan

1. Unit tests for the selection rule: held items with SELL, REDUCE, REVIEW, WATCH are shown; non-held items
   with SELL, REDUCE, WATCH, REVIEW, HOLD are not; non-held BUY and ADD are shown; held HOLD is still not
   shown.
2. Re-render the daily brief from the stored facts and check: no non-held SELL/REDUCE/WATCH/REVIEW/HOLD rows,
   BUY/ADD rows for other securities present where the analysis has them.
3. `python -m unittest discover -s tests -t .`. Known failures that are not yours:
   `test_german_pdfs_markdown_and_prompt` (pre-existing) and `test_chart_data` (other session).
4. Render the PDF and look at it (`pdftoppm -r 60`).

## Acceptance

- Only the rules above appear in the daily brief. The issue report is unchanged.
- Tests as above.
- A Log entry with: the path that carried non-held items, the change, the test result, and open items.

## Log

- 2026-11-11: spec written. Not started.
