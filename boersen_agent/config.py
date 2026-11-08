"""Configuration: TOML file plus environment variables for secrets."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Rule parameters. Values marked [U] are not stated in the newsletter and are best guesses;
# calibrate them with tools/calibrate.py against the published dashboard states.
DEFAULT_RULES: dict[str, Any] = {
    "nine_month_weeks": 39,        # [V] 9-month low on a weekly close = sell at the latest
    "rs_months": 15,               # [V] RS = price * 100 / 15-month median
    "rs_top_n": 200,               # [V] buy only from the 200 strongest
    "candidate_top_n": 30,         # how many top-RS stocks to consider for new ideas
    "max_new_candidates": 10,
    "kuv_normal_max": 2.0,         # [V] p9: "Normal wäre ein KUV von 0,6 bis 2,0"
    "kbv_fair_max": 4.0,           # [V] 8 Jun: KBV up to 4 for financials
    "near_stop_pct": 3.0,          # warn when price is within this % of the 9-month stop
    "stale_newsletter_days": 16,   # issues come every 14 days
    "no_new_high_weeks": 52,       # [V] 12+ months without a new high -> review
    "zins_smoothing_weeks": 22,    # [V] 17 Aug
    "nasdaq_sell_weeks": 26,       # [V] 16 Mar
    "nasdaq_buy_weeks": 26,        # [U]
    "dju_sell_weeks": 6,           # [V] 30 Mar
    "dju_buy_weeks": 9,            # [V] 22 Jun / 6 Jul
    "dax_sell_weeks": 6,           # [V] 22 Jun
    "dax_buy_weeks": 6,            # [U]
    "bond_weeks": 39,              # [V] 3 Aug (series [U]: default US 10y)
    "oil_weeks": 6,                # [V] negative on a new 6-week high; positive rule [U]
    "dollar_weeks": 15,            # [I] buy on a 15-week high; sell rule [U]
}

DEFAULT_SYMBOLS = {
    "yahoo": {
        "sp500": "^GSPC", "dax": "^GDAXI", "nasdaq": "^IXIC", "dow_utility": "^DJU",
        "brent": "BZ=F", "eurusd": "EURUSD=X", "fx_pattern": "EUR{cur}=X",
    },
    # Verify these in the EODHD docs / their symbol search before relying on them.
    "eodhd": {
        "sp500": "GSPC.INDX", "dax": "GDAXI.INDX", "nasdaq": "IXIC.INDX", "dow_utility": "DJU.INDX",
        "brent": "BZ.COMM", "eurusd": "EURUSD.FOREX", "fx_pattern": "EUR{cur}.FOREX",
    },
}

DEFAULT_STRATEGY_TARGETS = {"high_risk": 10.0, "stocks": 40.0, "safe": 50.0}
# The market strip at the top of the daily brief: label, data symbol, kind (index | usd | fx | yield).
DEFAULT_MARKET_ITEMS = [
    {"label": "S&P 500", "symbol": "^GSPC", "kind": "index"},
    {"label": "DAX", "symbol": "^GDAXI", "kind": "index"},
    {"label": "Nasdaq", "symbol": "^IXIC", "kind": "index"},
    {"label": "Brent", "symbol": "BZ=F", "kind": "usd"},
    {"label": "EUR/USD", "symbol": "EURUSD=X", "kind": "fx"},
    {"label": "US 10J", "symbol": "^TNX", "kind": "yield"},
]
MARKET_KINDS = {"index", "usd", "fx", "yield"}


@dataclass
class Config:
    data_dir: Path
    reports_dir: Path
    paperless_url: str | None
    paperless_token: str | None
    correspondent: str
    upload_reports: bool
    report_tags: list[int]
    provider: str
    provider_token: str | None
    auto_lookup: bool
    history_years: int
    symbols: dict[str, str]
    rules: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RULES))
    ai_enabled: bool = True
    ai_mode: str = "api"                    # api: call Claude via the API | handoff: prepare/finish for a Claude session
    ai_model: str = "claude-opus-5-5"
    ai_model_daily: str | None = None       # model for the daily brief; falls back to ai_model
    ai_model_issue: str | None = None       # model for the issue report; falls back to ai_model
    ai_effort_daily: str = "medium"
    ai_effort_issue: str = "high"
    ai_max_tokens: int = 32000
    ai_base_url: str | None = None          # e.g. LiteLLM pass-through: http://litellm:4000/anthropic
    ai_server_fallbacks: bool = True        # Anthropic server-side refusal fallback (turn off if a gateway rejects it)
    keep_markdown: bool = True
    report_language: str = "en"             # en | de: language of reports, charts and the AI's text
    strategy_targets: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_STRATEGY_TARGETS))
    high_risk_isins: list[str] = field(default_factory=list)
    fund_allocations: dict[str, dict[str, float]] = field(default_factory=dict)  # ISIN -> stock/bond/cash/other shares
    market_items: list[dict] = field(default_factory=lambda: [dict(i) for i in DEFAULT_MARKET_ITEMS])

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    def rule(self, key: str):
        return self.rules[key]


def _market_items(raw) -> list[dict]:
    """[[brief.market_items]] label / symbol / kind rows for the market strip; the defaults when absent."""
    if raw is None:
        return [dict(i) for i in DEFAULT_MARKET_ITEMS]
    if not isinstance(raw, list) or not raw:
        raise ValueError("brief.market_items must be a list of tables with label, symbol and kind")
    out = []
    for row in raw:
        missing = {"label", "symbol", "kind"} - set(row)
        if missing:
            raise ValueError(f"brief.market_items entry {row}: missing {sorted(missing)}")
        if row["kind"] not in MARKET_KINDS:
            raise ValueError(f"brief.market_items {row['label']}: kind must be one of {sorted(MARKET_KINDS)}")
        out.append({"label": str(row["label"]), "symbol": str(row["symbol"]), "kind": str(row["kind"])})
    return out


FUND_PARTS = ("stock", "bond", "cash", "other")


def _fund_allocations(raw: dict) -> dict[str, dict[str, float]]:
    """[fund_allocations] ISIN = { stock = 0.44, bond = 0.53, cash = 0.0, other = 0.03 } (shares of the fund).
    The four shares must add up to 1; an entry that does not is an error, not a guess."""
    if not isinstance(raw, dict):
        raise ValueError("fund_allocations must be a table keyed by ISIN")
    out: dict[str, dict[str, float]] = {}
    for isin, parts in raw.items():
        unknown = set(parts) - set(FUND_PARTS)
        if unknown:
            raise ValueError(f"fund_allocations {isin}: unknown keys {sorted(unknown)}")
        shares = {part: float(parts.get(part, 0.0)) for part in FUND_PARTS}
        if any(v < 0 for v in shares.values()) or abs(sum(shares.values()) - 1.0) > 0.005:
            raise ValueError(f"fund_allocations {isin}: the shares must be between 0 and 1 and add up to 1 "
                             f"(now {sum(shares.values()):.2f})")
        out[str(isin).upper()] = shares
    return out


def load(path: str | Path | None) -> Config:
    raw: dict[str, Any] = {}
    base = Path.cwd()
    if path:
        p = Path(path)
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        base = p.resolve().parent
    paths = raw.get("paths", {})
    pl = raw.get("paperless", {})
    mk = raw.get("market", {})
    provider = mk.get("provider", "yahoo")
    if provider not in DEFAULT_SYMBOLS and provider != "csv":
        raise ValueError(f"unknown market.provider {provider!r} (yahoo, eodhd or csv)")
    symbols = dict(DEFAULT_SYMBOLS.get(provider, DEFAULT_SYMBOLS["yahoo"]))
    symbols.update(mk.get("symbols", {}))
    rules = dict(DEFAULT_RULES)
    unknown = set(raw.get("rules", {})) - set(DEFAULT_RULES)
    if unknown:
        raise ValueError(f"unknown rule keys in config: {sorted(unknown)}")
    rules.update(raw.get("rules", {}))

    def rel(v: str) -> Path:
        q = Path(v).expanduser()
        return q if q.is_absolute() else base / q

    token_env = mk.get("token_env", "EODHD_API_TOKEN")
    ai = raw.get("ai", {})
    strategy = raw.get("strategy", {})
    unknown_strategy = set(strategy) - {"targets", "high_risk_isins"}
    if unknown_strategy:
        raise ValueError(f"unknown strategy keys: {sorted(unknown_strategy)}")
    if not isinstance(strategy.get("targets", {}), dict):
        raise ValueError("strategy.targets must be a table of allocation percentages")
    high_risk_isins = strategy.get("high_risk_isins", [])
    if not isinstance(high_risk_isins, list):
        raise ValueError("strategy.high_risk_isins must be a list")
    targets = dict(DEFAULT_STRATEGY_TARGETS)
    unknown_targets = set(strategy.get("targets", {})) - set(targets)
    if unknown_targets:
        raise ValueError(f"unknown strategy target keys: {sorted(unknown_targets)}")
    targets.update({key: float(value) for key, value in strategy.get("targets", {}).items()})
    if any(value < 0 for value in targets.values()) or abs(sum(targets.values()) - 100) > 0.01:
        raise ValueError("strategy.targets must be non-negative and add up to 100")
    fund_allocations = _fund_allocations(raw.get("fund_allocations", {}))
    market_items = _market_items(raw.get("brief", {}).get("market_items"))
    return Config(
        data_dir=rel(paths.get("data_dir", "data")),
        reports_dir=rel(paths.get("reports_dir", "reports")),
        paperless_url=os.environ.get(pl.get("url_env", "PAPERLESS_URL")) or pl.get("url"),
        paperless_token=os.environ.get(pl.get("token_env", "PAPERLESS_TOKEN")),
        correspondent=pl.get("correspondent", "Börsen Signale"),
        upload_reports=bool(pl.get("upload_reports", False)),
        report_tags=list(pl.get("report_tags", [])),
        provider=provider,
        provider_token=os.environ.get(token_env),
        auto_lookup=bool(mk.get("auto_lookup", True)),
        history_years=int(mk.get("history_years", 3)),
        symbols=symbols,
        rules=rules,
        ai_enabled=bool(ai.get("enabled", True)),
        ai_mode=ai.get("mode", "api"),
        ai_model=ai.get("model", "claude-opus-5-5"),
        ai_model_daily=ai.get("model_daily") or None,
        ai_model_issue=ai.get("model_issue") or None,
        ai_effort_daily=ai.get("effort_daily", "medium"),
        ai_effort_issue=ai.get("effort_issue", "high"),
        ai_max_tokens=int(ai.get("max_tokens", 32000)),
        ai_base_url=ai.get("base_url") or None,
        ai_server_fallbacks=bool(ai.get("server_fallbacks", True)),
        keep_markdown=bool(paths.get("keep_markdown", True)),
        report_language=str(raw.get("report", {}).get("language", "en")),
        strategy_targets=targets,
        high_risk_isins=[str(isin).upper() for isin in high_risk_isins],
        fund_allocations=fund_allocations,
        market_items=market_items,
    )
