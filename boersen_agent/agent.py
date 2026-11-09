"""Orchestration: newsletter ingestion, evaluation, reports."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import attachments, brief, charts, mailer, pdfreport, pricechart, report, weekly
from . import rules as R
from .i18n import _, num
from .names import nice_name
from .analyst import AnalystError, analyse, build_facts
from .marketdata import DataError
from .newsletter import Issue, issue_age_days, name_key, parse_pdf
from .portfolio import holdings, valuation
from .signals import compute_market, zins_at_dates
from .suggest import UniverseIndex, evaluate, held_weeks_since, live_metrics, sector_kuv

LOOKUP_BUDGET = 25


@dataclass
class Context:
    issue: dict | None
    dashboard: dict | None
    market: object
    suggestions: list
    portfolio: dict
    previous: dict
    notes: list[str]
    issue_rows: dict


class Agent:
    def __init__(self, cfg, store, market, paperless=None, now: datetime | None = None, ai_client=None):
        self.cfg, self.store, self.market, self.paperless = cfg, store, market, paperless
        self.ai_client = ai_client
        from . import i18n
        i18n.set_lang(cfg.report_language)
        self.now = now or datetime.now()
        self.today = self.now.date()
        self._lookups = 0
        self.notes: list[str] = []

    # ------------------------------------------------------------------ ingestion
    def _correspondent_id(self) -> int:
        for c in self.paperless.list_correspondents():
            if c["name"].strip().lower() == self.cfg.correspondent.strip().lower():
                return c["id"]
        raise RuntimeError(f"Paperless correspondent {self.cfg.correspondent!r} not found")

    def ingest(self) -> list[str]:
        """Pull newsletters from Paperless that were not seen before. Returns new issue keys."""
        if self.paperless is None:
            raise RuntimeError("Paperless is not configured (PAPERLESS_URL / PAPERLESS_TOKEN)")
        cid = self._correspondent_id()
        folder = self.cfg.data_dir / "newsletters"
        folder.mkdir(parents=True, exist_ok=True)
        new = []
        for doc in self.paperless.iter_documents(correspondent__id=cid, ordering="created"):
            if self.store.has_doc(doc["id"]):
                continue
            pdf = folder / f"{doc['created'][:10]}_{doc['id']}.pdf"
            if not pdf.exists():
                self.paperless.download_document(doc["id"], pdf)
            key = self.ingest_file(pdf, doc_id=doc["id"])
            if key:
                new.append(key)
        return new

    def ingest_file(self, pdf: Path, doc_id: int | None = None) -> str | None:
        try:
            issue: Issue = parse_pdf(Path(pdf))
        except Exception as e:  # noqa: BLE001 - keep going with the other documents
            if doc_id is not None:
                self.store.mark_doc(doc_id, None, f"parse failed: {e}")
            self.notes.append(f"could not parse {Path(pdf).name}: {e}")
            return None
        if self.store.has_issue(issue.key):
            if doc_id is not None:
                self.store.mark_doc(doc_id, issue.key, "duplicate upload")
            return None
        self.store.save_issue(issue, doc_id, self.now.isoformat(timespec="seconds"))
        if doc_id is not None:
            self.store.mark_doc(doc_id, issue.key, "ingested")
        return issue.key

    # ------------------------------------------------------------------ symbols
    def _lookup(self, query: str):
        if not self.cfg.auto_lookup or self._lookups >= LOOKUP_BUDGET:
            return None
        self._lookups += 1
        try:
            hits = self.market.provider.search(query)
        except DataError as e:
            self.market.errors.append(f"symbol lookup failed for {query}: {e}")
            return None
        return hits[0] if hits else None

    def symbol_for(self, isin: str | None, name: str | None) -> tuple[str | None, bool]:
        if isin:
            row = self.store.symbol_for_isin(isin)
            if row:
                return row["symbol"], bool(row["verified"])
            hit = self._lookup(isin)
            if hit:
                self.store.set_symbol(isin, hit.symbol, name or hit.name, hit.currency, "lookup", False)
                return hit.symbol, False
        if name:
            k = name_key(name)
            row = self.store.symbol_for_name(k)
            if row:
                return row["symbol"], bool(row["verified"])
            if not isin:
                hit = self._lookup(name)
                if hit:
                    self.store.set_name_symbol(k, name, hit.symbol, "lookup", False)
                    return hit.symbol, False
        return None, False

    # ------------------------------------------------------------------ evaluation
    def _live(self, symbol: str | None):
        if not symbol:
            return None
        daily = self.market.daily_with_quote(symbol)
        q = self.market.quote(symbol)
        return live_metrics(daily, self.now, self.cfg.rule("nine_month_weeks"), self.cfg.rule("rs_months"),
                            q.previous_close if q else None)

    def context(self, issue_key: str | None = None, with_candidates: bool = True,
                newsletter_scope: bool = True) -> Context:
        iss = (self.store.latest_issue() if issue_key is None else
               next((dict(r) for r in self.store.db.execute("SELECT * FROM issues WHERE issue_key=?",
                                                              (issue_key,))), None))
        dash = json.loads(iss["dashboard"]) if iss else None
        rows = {t: self.store.rows(t, iss["issue_key"]) if iss else [] for t in ("dispo", "universe", "sectors", "calls")}
        uidx = UniverseIndex(rows["universe"])
        age = issue_age_days(iss["issue_date"], self.today) if iss else None
        mstate = compute_market(self.market, self.cfg, dash, self.now, iss["issue_date"] if iss else None)

        hold = holdings(self.store)
        fund_allocations = {}
        for h in hold:
            if h.asset != "fund":
                continue
            symbol, _ = self._resolve_holding(h)
            # the split from config.toml ([fund_allocations]) wins over the data provider
            allocation = self.cfg.fund_allocations.get(h.key.upper()) or (
                self.market.asset_allocation(symbol) if symbol else None)
            if allocation:
                fund_allocations[h.key] = allocation
            else:
                source = self.market.provider.name
                self.notes.append(f"fund allocation unavailable for {h.name} ({source}); exposure remains unclassified")
        port = valuation(hold, self.market, self.cfg, lambda h: self._resolve_holding(h), fund_allocations)
        dispo_by_isin = {d["isin"]: d for d in rows["dispo"]}
        sug, seen = [], set()

        def add(key, name, isin, in_port, dispo, held_weeks=None, candidate=False, uni=None, match=0.0,
                fund=False, symbol=None):
            if key in seen:
                return
            seen.add(key)
            sym, ver = (symbol, True) if symbol else self.symbol_for(isin, name)
            if uni is None and not fund:
                uni, match = uidx.match(name)
            sk = sector_kuv(dispo.get("sector") if dispo else None, rows["sectors"])
            live = self._live(sym)
            s = evaluate(key=key, name=name, isin=isin, symbol=sym, symbol_verified=ver, in_portfolio=in_port,
                         dispo=dispo, uni=uni, uni_match=match,
                         list_size=uidx.list_sizes.get(uni["list"]) if uni else None, sect_kuv=sk,
                         live=live, market=mstate, cfg=self.cfg, newsletter_age=age,
                         held_weeks=held_weeks, candidate=candidate)
            if fund:
                s.reasons.append({"text": "fund/ETF: only the price rules apply (no newsletter stock data)",
                                  "marker": "", "pts": 0})
            sug.append(s)

        for h in hold:
            if h.cash_like:
                continue
            d = dispo_by_isin.get(h.isin) if h.isin else None
            if d is None and rows["dispo"] and h.asset == "stock":
                d = next((x for x in rows["dispo"] if name_key(x["name"]) == name_key(h.name)), None)
            add(h.isin or h.symbol, h.name, h.isin, True, d, held_weeks_since(h.first_date, self.today),
                fund=h.asset == "fund", symbol=h.symbol)
        if newsletter_scope:
            for d in rows["dispo"]:
                add(d["isin"], d["name"], d["isin"], False, d)
        if with_candidates and uidx.rows:
            dispo_keys = {name_key(d["name"]) for d in rows["dispo"]}
            picks = 0
            for r in uidx.rows[: self.cfg.rule("candidate_top_n")]:
                if picks >= self.cfg.rule("max_new_candidates"):
                    break
                if r["ht"] != "H" or name_key(r["name"]) in dispo_keys or name_key(r["name"]) in seen:
                    continue
                kuv_ok = r["kuv"] is not None and r["kuv"] <= self.cfg.rule("kuv_normal_max")
                kbv_ok = r["kuv"] is None and r["kbv"] is not None and r["kbv"] <= self.cfg.rule("kbv_fair_max")
                if not (kuv_ok or kbv_ok):
                    continue
                picks += 1
                add("name:" + name_key(r["name"]), r["name"], None, False, None, candidate=True, uni=r, match=1.0)

        prev = self.store.previous_suggestions("daily", self.today.isoformat())
        return Context(iss, dash, mstate, sug, port, prev, self.notes + self.market.errors, rows)

    def _resolve_holding(self, h):
        if h.symbol:
            return h.symbol, True
        return self.symbol_for(h.isin, h.name)

    # ------------------------------------------------------------------ AI
    def _analyse(self, ctx, scope: str, changes=None, pdf: Path | None = None):
        if not self.cfg.ai_enabled:
            return None, "disabled in config ([ai] enabled = false)"
        if self.cfg.ai_mode == "handoff":
            return None, "hand-off mode: run `prepare`, write the analysis, then `finish`"
        prev = self.store.last_analysis(before=None)
        facts = build_facts(ctx, scope, self.now, changes, prev)
        effort = self.cfg.ai_effort_issue if scope == "issue" else self.cfg.ai_effort_daily
        try:
            model = (self.cfg.ai_model_issue if scope == "issue" else self.cfg.ai_model_daily) or self.cfg.ai_model
            a = analyse(facts, model=model, effort=effort, pdf=pdf, client=self.ai_client,
                        max_tokens=self.cfg.ai_max_tokens, base_url=self.cfg.ai_base_url,
                        server_fallbacks=self.cfg.ai_server_fallbacks)
        except AnalystError as e:
            return None, str(e)
        self.store.save_analysis(self.today.isoformat(), scope, a, self.now.isoformat(timespec="seconds"))
        return a, None

    # ------------------------------------------------------------------ charts
    def _charts(self, ctx, name: str, issue_scope: bool) -> dict:
        folder = self.cfg.reports_dir / ".charts"
        folder.mkdir(parents=True, exist_ok=True)
        out: dict = {}

        def go(key, fn, *args):
            try:
                out[key] = fn(*args, folder / f"{name}_{key}.png")
            except Exception as e:  # noqa: BLE001 - a broken chart must not stop the report
                self.notes.append(f"chart {key} failed: {e}")

        go("allocation", charts.allocation, ctx.portfolio["rows"])
        held = [s for s in ctx.suggestions if s.in_portfolio and s.live]
        try:
            out["stop_distance"] = charts.stop_distance(
                [{"name": s.name, "dist": s.live.get("dist_to_stop_pct"),
                  "breach": s.live.get("stop_breach_confirmed")} for s in held],
                folder / f"{name}_stop_distance.png", self.cfg.rule("near_stop_pct"))
        except Exception as e:  # noqa: BLE001 - a broken chart must not stop the report
            self.notes.append(f"position charts failed: {e}")
        go("value_history", charts.value_history, self.store.snapshots())
        if issue_scope:
            hist = [dict(r) for r in self.store.db.execute("SELECT issue_date, dashboard FROM issues ORDER BY issue_date")]
            gesamt_rows = [{"date": h["issue_date"], "score": json.loads(h["dashboard"]).get("gesamt", {}).get("score")}
                           for h in hist]
            out["gesamt_rows"] = gesamt_rows  # raw data for the SVG charts in the report
            go("gesamt_history", charts.gesamt_history, gesamt_rows)
            comp = zins_at_dates(self.market, self.cfg, [h["issue_date"] for h in hist])
            zins_rows = [(h["issue_date"], json.loads(h["dashboard"]).get("zinsstruktur_value"), comp.get(h["issue_date"]))
                         for h in hist]
            out["zins_rows"] = zins_rows
            go("zins_history", charts.zins_history, zins_rows)
            groups = {"newsletter buy (K)": [s.universe for s in ctx.suggestions if s.newsletter_rec == "K" and s.universe],
                      "your holdings": [s.universe for s in ctx.suggestions if s.in_portfolio and s.universe],
                      "new candidates": [s.universe for s in ctx.suggestions if s.newsletter_rec is None
                                         and not s.in_portfolio and s.universe]}
            out["scatter"] = {"universe": ctx.issue_rows["universe"], "highlights": groups,
                              "top_n": self.cfg.rule("rs_top_n"), "kuv_max": self.cfg.rule("kuv_normal_max")}
            go("rs_kuv", charts.rs_kuv, ctx.issue_rows["universe"], groups, self.cfg.rule("rs_top_n"),
               self.cfg.rule("kuv_normal_max"))
        return {k: v for k, v in out.items() if v}

    def _idea_series(self, ctx, analysis) -> list[dict]:
        """The new ideas of the AI analysis, with their weekly closes where a market symbol exists."""
        if not analysis:
            return []
        calls = analysis.calls_by_key()
        strategy = self.ctx_strategy(ctx)
        out = []
        for s in ctx.suggestions:
            call = (analysis.data.get("new_ideas") and next(
                (c for c in analysis.data["new_ideas"] if c["key"] == s.key), None))
            if not call:
                continue
            closes, stops, dates, avg = [], [], None, None
            if s.symbol:
                try:
                    done = R.weekly(self.market.daily_with_quote(s.symbol), self.now)[0]
                    closes, stops = brief._series([c for _d, c in done], self.cfg.rule("nine_month_weeks"))
                    dates = brief._chart_dates([d for d, _c in done], len(closes)) if closes else None
                    avg = pricechart.window_averages([c for _d, c in done], brief.CHART_WEEKS) if closes else None
                except Exception as e:  # noqa: BLE001 - a missing chart must not stop the report
                    self.notes.append(f"no price chart for {s.name}: {e}")
            uni = s.universe or {}
            live = s.live or {}
            out.append({"name": nice_name(s.name), "action": call["action"], "closes": closes, "stops": stops,
                        "dates": dates, "averages": avg, "today": live.get("price"), "caption": "", "key": s.key, "isin": s.isin,
                        "rule_action": s.action, "rule_confidence": s.confidence,
                        "rs": uni.get("rs"), "kuv": uni.get("kuv"), "kbv": uni.get("kbv"),
                        "newsletter_rec": s.newsletter_rec, "in_fund": False,
                        "stock_share": strategy.get("stocks"), "stock_target": strategy.get("stocks_target")})
        return out

    def ctx_strategy(self, ctx) -> dict:
        """Actual stock share and the target, for the strategy fit in the buy sections."""
        actual = (ctx.portfolio.get("strategy") or {}).get("actual_pct") or {}
        return {"stocks": actual.get("stocks"), "stocks_target": self.cfg.strategy_targets.get("stocks")}

    def _stock_series(self, ctx, analysis) -> list[dict]:
        """Weekly closes and rolling stop for every stock labelled SELL, REDUCE, REVIEW or BUY (AI call or rule)."""
        calls = analysis.calls_by_key() if analysis else {}
        order = {a: i for i, a in enumerate(self.CHARTED_ACTIONS)}
        chosen = []
        for s in ctx.suggestions:
            ai_action = (calls.get(s.key) or {}).get("action")
            if s.action in order or ai_action in order:
                best = min([order[a] for a in (s.action, ai_action) if a in order])
                chosen.append((best, s, ai_action or s.action))
        out = []
        for _rank, s, code in sorted(chosen, key=lambda t: (t[0], -t[1].confidence)):
            if not s.symbol:
                self.notes.append(f"no price chart for {s.name}: no market symbol")
                continue
            try:
                done = R.weekly(self.market.daily_with_quote(s.symbol), self.now)[0]
            except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the report
                self.notes.append(f"no price chart for {s.name}: {e}")
                continue
            closes, stops = brief._series([c for _d, c in done], self.cfg.rule("nine_month_weeks"))
            if not closes:
                continue
            dates = brief._chart_dates([d for d, _c in done], len(closes))
            live = s.live or {}
            price, stop = live.get("price"), live.get("stop")
            caption = (f"{_('Price')} {num(price, 2)} · {_('stop')} {num(stop, 2)}"
                       if price is not None and stop is not None else "")
            uni = s.universe or {}
            strategy = self.ctx_strategy(ctx)
            out.append({"name": nice_name(s.name), "action": code, "closes": closes, "stops": stops,
                        "dates": dates, "averages": pricechart.window_averages([c for _d, c in done], brief.CHART_WEEKS),
                        "today": price, "caption": caption, "key": s.key, "isin": s.isin,
                        "rule_action": s.action, "rule_confidence": s.confidence,
                        "rs": uni.get("rs"), "kuv": uni.get("kuv"), "kbv": uni.get("kbv"),
                        "newsletter_rec": s.newsletter_rec, "in_fund": s.newsletter_rec in ("K", "H"),
                        "stock_share": strategy.get("stocks"), "stock_target": strategy.get("stocks_target")})
        return out

    def _stock_charts(self, ctx, name: str, analysis) -> list[Path]:
        """Price charts (weekly closes, 9-month stop) for every stock whose AI call or rule label is
        SELL, REDUCE, REVIEW or BUY, in pages of six."""
        calls = analysis.calls_by_key() if analysis else {}
        order = {a: i for i, a in enumerate(self.CHARTED_ACTIONS)}
        chosen = []
        for s in ctx.suggestions:
            ai_action = (calls.get(s.key) or {}).get("action")
            if s.action in order or ai_action in order:
                best = min([order[a] for a in (s.action, ai_action) if a in order])
                chosen.append((best, s))
        panels = []
        for _rank, s in sorted(chosen, key=lambda t: (t[0], -t[1].confidence)):
            if not s.symbol:
                self.notes.append(f"no price chart for {s.name}: no market symbol")
                continue
            try:
                weekly = R.weekly(self.market.daily_with_quote(s.symbol), self.now)[0]
            except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the report
                self.notes.append(f"no price chart for {s.name}: {e}")
                continue
            panels.append({"name": s.name, "weekly": weekly})
        folder = self.cfg.reports_dir / ".charts"
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(0, len(panels), 4):
            try:
                p = charts.price_vs_stop(panels[i:i + 4], folder / f"{name}_stocks_{i // 4 + 1}.png",
                                         self.cfg.rule("nine_month_weeks"))
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"stock charts failed: {e}")
                p = None
            if p:
                paths.append(p)
        return paths

    # ------------------------------------------------------------------ outputs
    def _publish(self, pdf: Path, title: str) -> None:
        if self.cfg.upload_reports and self.paperless is not None:
            try:
                self.paperless.upload_document(pdf, title=title, tags=self.cfg.report_tags or None)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"upload to Paperless failed: {e}")
        if self.cfg.email_enabled:
            try:
                mailer.send_report(self.cfg, pdf, title)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"e-mail failed: {e}")

    def _conclusion(self, key: str) -> dict | None:
        """The closing commentary of the issue's newsletter page, shown at the bottom of page 1."""
        src = self._issue_pdf_path(key)
        if not (self.cfg.conclusion_page and src):
            return None
        try:
            block = attachments.conclusion_block(src, self.cfg.conclusion_page)
        except Exception as e:  # noqa: BLE001 - the report is still complete without it
            self.notes.append(f"newsletter conclusion not shown: {e}")
            return None
        block["issue"] = key
        return block

    def _issue_pdf_path(self, issue_key: str) -> Path | None:
        r = self.store.db.execute("SELECT doc_id FROM issues WHERE issue_key=?", (issue_key,)).fetchone()
        folder = self.cfg.data_dir / "newsletters"
        if r and r[0]:
            hits = sorted(folder.glob(f"*_{r[0]}.pdf"))
            if hits:
                return hits[0]
            if self.paperless is not None:
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / f"issue-{issue_key}_{r[0]}.pdf"
                try:
                    return self.paperless.download_document(r[0], dest)
                except Exception as e:  # noqa: BLE001
                    self.notes.append(f"could not download the issue PDF from Paperless: {e}")
        return None

    def biweekly(self, issue_key: str | None = None, pdf: Path | None = None, precomputed=None) -> Path:
        ctx = self.context(issue_key, with_candidates=True, newsletter_scope=True)
        if not ctx.issue:
            raise RuntimeError("no newsletter ingested yet")
        key = ctx.issue["issue_key"]
        prev_issue = self.store.latest_issue(before=ctx.issue["issue_date"])
        prev_rows = self.store.rows("dispo", prev_issue["issue_key"]) if prev_issue else []
        changes = report.dispo_changes(prev_rows, ctx.issue_rows["dispo"]) if prev_issue else []
        analysis, err = precomputed or self._analyse(ctx, "issue", changes, pdf or self._issue_pdf_path(key))
        figs = self._charts(ctx, key, True)
        figs["stock_series"] = self._stock_series(ctx, analysis)
        figs["idea_series"] = self._idea_series(ctx, analysis)
        ctx.notes = list(dict.fromkeys(ctx.notes + self.notes))
        self.store.save_suggestions(self.today.isoformat(), "issue:" + key, [s.as_dict() for s in ctx.suggestions])
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        if self.cfg.keep_markdown:
            (self.cfg.reports_dir / f"{key}_newsletter-report.md").write_text(
                report.biweekly(ctx, prev_issue, prev_rows, self.cfg, self.now, analysis, err), encoding="utf-8")
        figs["conclusion"] = self._conclusion(key)
        out = weekly.render_issue(ctx, analysis, err, self.now, changes, figs, ctx.notes, pdfreport.DISCLAIMER,
                                  self.cfg.reports_dir / f"{key}_newsletter-report.pdf")
        self._publish(out, f"Börsen-Agent Bericht Ausgabe {key}")
        return out

    def daily(self, precomputed=None) -> Path:
        ctx = self.context(with_candidates=False, newsletter_scope=False)
        day = self.today.isoformat()
        self.store.save_snapshot(day, ctx.portfolio["total_eur"],
                                 {"rows": ctx.portfolio["rows"], "equity_share": ctx.portfolio["equity_share"]})
        analysis, err = precomputed or self._analyse(ctx, "daily")
        ctx.notes = list(dict.fromkeys(ctx.notes + self.notes))
        self.store.save_suggestions(day, "daily", [s.as_dict() for s in ctx.suggestions])
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        if self.cfg.keep_markdown:
            (self.cfg.reports_dir / f"{day}_daily-brief.md").write_text(
                report.daily(ctx, self.cfg, self.now, analysis, err), encoding="utf-8")
        items = report.daily_action_items(ctx, analysis)

        weekly_done: dict[str, list | None] = {}

        def weekly_for(symbol: str):
            if symbol not in weekly_done:
                try:
                    weekly_done[symbol] = R.weekly(self.market.daily_with_quote(symbol), self.now)[0]
                except Exception as e:  # noqa: BLE001 - a missing history must not stop the brief
                    self.notes.append(f"no price history for {symbol}: {e}")
                    weekly_done[symbol] = None
            return weekly_done[symbol]

        def closes_for(symbol: str) -> list[float] | None:
            done = weekly_for(symbol)
            return [c for _d, c in done] if done else None

        def dates_for(symbol: str) -> list[str] | None:
            done = weekly_for(symbol)
            return [d for d, _c in done] if done else None

        data = brief.daily_data(ctx, analysis, self.now, items[:brief.MAX_ACTIONS], closes_for,
                                self.cfg.strategy_targets, len(items), self.cfg.rule("nine_month_weeks"),
                                self.cfg.report_language,
                                market=brief.market_quotes(self.market.quote, self.now, self.cfg.market_items),
                                dates_for=dates_for)
        out = self.cfg.reports_dir / f"{day}_daily-brief.pdf"
        brief.render_pdf(data, out.with_suffix(".html"), out)
        self._publish(out, f"Börsen-Agent Tagesbrief {day}")
        return out

    # ------------------------------------------------------------------ hand-off mode
    def prepare(self, out: Path, issue_key: str | None = None) -> dict:
        """Ingest new issues and write, per report, the fact pack, prompt and schema for an outside analyst
        (e.g. the Claude session that runs this command). Returns the task list."""
        from .analyst import SCHEMA, system_prompt
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        new = self.ingest() if self.paperless is not None else []
        (out / "prompt.md").write_text(system_prompt(), encoding="utf-8")
        (out / "schema.json").write_text(json.dumps(SCHEMA, indent=1), encoding="utf-8")
        prev = self.store.last_analysis()
        tasks = []
        if new or issue_key:
            key = issue_key or new[-1]
            ctx = self.context(key, with_candidates=True, newsletter_scope=True)
            prev_issue = self.store.latest_issue(before=ctx.issue["issue_date"])
            changes = (report.dispo_changes(self.store.rows("dispo", prev_issue["issue_key"]), ctx.issue_rows["dispo"])
                       if prev_issue else [])
            facts = build_facts(ctx, "issue", self.now, changes, prev)
            pdf = self._issue_pdf_path(key)
            txt = None
            if pdf:
                try:
                    from .newsletter import page_count, page_text
                    n = page_count(pdf)
                    pages = [page_text(pdf, p, layout=p >= 6) for p in range(1, min(n, 8) + 1)]
                    txt = out / f"issue-{key}_newsletter.txt"
                    txt.write_text("\n\f".join(pages), encoding="utf-8")   # pages 1-8: text, dashboard, Dispo-Liste
                except Exception as e:  # noqa: BLE001
                    self.notes.append(f"could not extract the newsletter text: {e}")
            tasks.append({"scope": "issue", "issue": key, "facts": str(out / f"issue-{key}_facts.json"),
                          "newsletter_pdf": str(pdf) if pdf else None,
                          "newsletter_text": str(txt) if txt else None,
                          "output": str(out / f"issue-{key}_analysis.json")})
            Path(tasks[-1]["facts"]).write_text(json.dumps(facts, ensure_ascii=False, indent=1, default=str), "utf-8")
        ctx = self.context(with_candidates=False, newsletter_scope=False)
        facts = build_facts(ctx, "daily", self.now, None, prev)
        tasks.append({"scope": "daily", "facts": str(out / "daily_facts.json"), "newsletter_pdf": None,
                      "output": str(out / "daily_analysis.json")})
        Path(tasks[-1]["facts"]).write_text(json.dumps(facts, ensure_ascii=False, indent=1, default=str), "utf-8")
        manifest = {"prompt": str(out / "prompt.md"), "schema": str(out / "schema.json"), "tasks": tasks,
                    "notes": list(dict.fromkeys(self.notes + self.market.errors))}
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
        return manifest

    def finish(self, out: Path, model: str = "claude-code-session") -> dict:
        """Validate the analyses written for `prepare`'s tasks, then render and publish the reports.
        A missing or invalid analysis still produces the rule-based report, with the reason."""
        from .analyst import Analysis, validate
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        reports, problems, done = [], [], {}
        for t in manifest["tasks"]:
            facts = json.loads(Path(t["facts"]).read_text(encoding="utf-8"))
            pre = (None, "no analysis file was written")
            outp = Path(t["output"])
            if outp.exists():
                try:
                    data, warn = validate(json.loads(outp.read_text(encoding="utf-8")), facts)
                    missing = [k for k in ("headline", "summary", "market_view", "position_calls") if k not in data]
                    if missing:
                        raise ValueError("missing fields: " + ", ".join(missing))
                    for k in ("newsletter_picks", "new_ideas", "risks", "questions_for_user"):
                        data.setdefault(k, [])
                    data.setdefault("notes_for_next_run", "")
                    a = Analysis(data, model, {}, warn)
                    self.store.save_analysis(self.today.isoformat(), t["scope"], a, self.now.isoformat(timespec="seconds"))
                    pre = (a, None)
                    done[t["scope"]] = data
                except (ValueError, KeyError, TypeError) as e:
                    pre = (None, f"analysis file invalid: {e}")
            if pre[0] is None:
                problems.append(f"{t['scope']}: {pre[1]}")
            path = self.biweekly(t["issue"], precomputed=pre) if t["scope"] == "issue" else self.daily(precomputed=pre)
            reports.append(str(path))
        last = done.get("daily") or done.get("issue") or {}
        return {"reports": reports, "problems": problems, "headline": last.get("headline"),
                "alerts": [f"{c['key']}: {c['action']} ({c['confidence']})" for c in last.get("position_calls", [])
                           if c["action"] in ("SELL", "REDUCE", "WATCH")]}

    def run(self) -> list[Path]:
        out = []
        if self.paperless is not None:
            new = self.ingest()
            if new:
                out.append(self.biweekly(new[-1]))
        out.append(self.daily())
        return out


def today_str(d: date | None = None) -> str:
    return (d or date.today()).isoformat()
