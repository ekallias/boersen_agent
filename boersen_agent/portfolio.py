"""Portfolio: initial CSV import, recorded trades, holdings and EUR valuation.

The agent cannot see your broker. It tracks what you import and every trade you record with
`portfolio trade`. Cash rows (symbol CASH) are optional; if present, trades in that currency
adjust them.

Two CSV layouts are accepted:
  generic:  isin,symbol,name,quantity,avg_price,currency,buy_date[,asset]
            - isin or symbol per row; symbol in your market-data provider's format
            - CASH rows: symbol=CASH, quantity=amount, avg_price=1, currency=EUR
  broker:   German "Bestandsaufstellung" export, semicolon-separated:
            Bezeichnung;WKN;ISIN;Währung;Einstandskurs;Bestand (German number format, no buy dates)
Asset classes: stock | fund | bond | cash (detected from ISIN/name unless given). Bonds are quoted in
percent of nominal (quantity = nominal) and counted as cash-like, as the newsletter does with its
Bund position.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REQUIRED = {"quantity", "currency"}


@dataclass
class Holding:
    key: str
    isin: str | None
    symbol: str | None
    name: str
    quantity: float
    avg_price: float
    currency: str
    first_date: str
    realized: float = 0.0
    asset: str = "stock"

    @property
    def is_cash(self) -> bool:
        return self.asset == "cash" or (self.symbol or "").upper() == "CASH"

    @property
    def cash_like(self) -> bool:
        return self.is_cash or self.asset == "bond"


def classify(name: str, isin: str | None, symbol: str | None) -> str:
    if (symbol or "").upper() == "CASH":
        return "cash"
    n = (name or "").upper()
    if re.search(r"\bANLEIHE\b|\bBOND\b|\bBUND\b|^\d+([,.]\d+)?\s*%", n):
        return "bond"
    if (isin or "")[:2] in ("LU", "IE") or re.search(r"FUNDS?\b|\bETF\b|UCITS|INH\.?-?ANT|FONDS|\bSWAP\b", n):
        return "fund"
    return "stock"


def parse_number(v) -> float:
    s = str(v).strip().replace("\u00a0", "").replace(" ", "")
    if "," in s:                       # German format: 1.200,50
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _f(v: str | None, field: str, line: int) -> float:
    try:
        return parse_number(v)
    except (TypeError, ValueError):
        raise ValueError(f"line {line}: {field} must be a number, got {v!r}") from None


BROKER_COLS = {"bezeichnung": "name", "wkn": "wkn", "isin": "isin", "währung": "currency",
               "einstandskurs": "avg_price", "bestand": "quantity"}


def _read_rows(path: Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delim = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    broker = {"bezeichnung", "bestand"} <= set(fields)
    rows = []
    for r in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if broker:
            r = {BROKER_COLS.get(k, k): v for k, v in r.items()}
        rows.append(r)
    if not broker:
        missing = REQUIRED - set(fields)
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
    return rows


def import_csv(store, path: Path, today: date, replace: bool = False) -> int:
    existing = [t for t in store.transactions() if t["kind"] in ("initial", "cash")]
    if existing and not replace:
        raise ValueError("portfolio already imported; use --replace to start over (drops all transactions)")
    rows = []
    for i, r in enumerate(_read_rows(path), start=2):
        isin = r.get("isin") or None
        symbol = r.get("symbol") or None
        if not isin and not symbol:
            raise ValueError(f"line {i}: needs an isin or a symbol")
        name = r.get("name") or symbol or isin
        asset = (r.get("asset") or classify(name, isin, symbol)).lower()
        cash = asset == "cash"
        note = "import" + ("" if r.get("buy_date") else "; buy date unknown") + (f"; WKN {r['wkn']}" if r.get("wkn") else "")
        rows.append(dict(
            date=r.get("buy_date") or today.isoformat(), kind="cash" if cash else "initial", isin=isin,
            symbol=symbol, name=name, quantity=_f(r.get("quantity"), "quantity", i),
            price=1.0 if cash else _f(r.get("avg_price"), "avg_price", i),
            currency=(r.get("currency") or "EUR").upper(), fee=0.0, note=note, asset=asset))
    if replace:
        store.db.execute("DELETE FROM transactions")
        store.db.commit()
    for t in rows:
        store.add_transaction(**t)
        if t["isin"] and t["symbol"] and t["kind"] == "initial":
            store.set_symbol(t["isin"], t["symbol"], t["name"], t["currency"], "user", True)
    return len(rows)


def record_trade(store, kind: str, quantity: float, price: float, currency: str, today: date, *,
                 isin=None, symbol=None, name=None, fee=0.0, when: str | None = None) -> None:
    if kind not in ("buy", "sell"):
        raise ValueError("kind must be buy or sell")
    if not isin and not symbol:
        raise ValueError("give --isin or --symbol")
    if quantity <= 0 or price < 0:
        raise ValueError("quantity must be > 0 and price >= 0")
    if kind == "sell":
        h = {x.key: x for x in holdings(store)}.get(isin or symbol)
        if not h or h.quantity + 1e-9 < quantity:
            raise ValueError(f"cannot sell {quantity}: holding is {h.quantity if h else 0}")
    known = {x.key: x for x in holdings(store)}.get(isin or symbol)
    asset = known.asset if known else classify(name or "", isin, symbol)
    store.add_transaction(date=when or today.isoformat(), kind=kind, isin=isin, symbol=symbol, name=name,
                          quantity=quantity, price=price, currency=currency.upper(), fee=fee, note=None,
                          asset=asset)
    if isin and symbol:
        store.set_symbol(isin, symbol, name, currency.upper(), "user", True)


def holdings(store) -> list[Holding]:
    pos: dict[str, Holding] = {}
    cash: dict[str, Holding] = {}
    for t in store.transactions():
        cur = t["currency"]
        if t["kind"] == "cash":
            c = cash.setdefault(cur, Holding(f"CASH:{cur}", None, "CASH", f"Cash {cur}", 0, 1, cur, t["date"],
                                             asset="cash"))
            c.quantity += t["quantity"]
            continue
        key = t["isin"] or t["symbol"]
        h = pos.get(key)
        if h is None:
            h = pos[key] = Holding(key, t["isin"], t["symbol"], t["name"] or key, 0, 0, cur, t["date"],
                                   asset=t.get("asset") or "stock")
        h.symbol = h.symbol or t["symbol"]
        h.isin = h.isin or t["isin"]
        q, p, fee = t["quantity"], t["price"], t["fee"] or 0
        unit = 0.01 if h.asset == "bond" else 1.0   # bonds: price in % of nominal
        if t["kind"] in ("initial", "buy"):
            total = h.avg_price * h.quantity + q * p + fee
            h.quantity += q
            h.avg_price = total / h.quantity if h.quantity else 0
            if t["kind"] == "buy" and cur in cash:
                cash[cur].quantity -= q * p * unit + fee
        elif t["kind"] == "sell":
            h.realized += (p - h.avg_price) * q * unit - fee
            h.quantity -= q
            if cur in cash:
                cash[cur].quantity += q * p * unit - fee
    out = [h for h in pos.values() if h.quantity > 1e-9]
    return sorted(out, key=lambda h: h.name.lower()) + list(cash.values())


def fx_to_eur(market, cfg, currency: str | None) -> float | None:
    """Units of `currency` per 1 EUR (1.0 for EUR). GBp/GBX quotes are in pence."""
    if not currency or currency.upper() == "EUR":
        return 1.0
    cur, scale = currency, 1.0
    if currency in ("GBp", "GBX"):
        cur, scale = "GBP", 100.0
    sym = cfg.symbols.get("fx_pattern", "EUR{cur}=X").format(cur=cur.upper())
    q = market.quote(sym)
    return q.price * scale if q else None


def valuation(hold: list[Holding], market, cfg, resolve, fund_allocations: dict | None = None) -> dict:
    """resolve(holding) -> (symbol, verified). Per-position values and totals in EUR. Bonds without a
    quote are valued at cost (flagged). Fund equity exposure uses provider data; unavailable fund mix
    is unclassified rather than assumed to be equity."""
    fund_allocations = fund_allocations or {}
    high_risk_isins = {str(i).upper() for i in getattr(cfg, "high_risk_isins", [])}
    targets = getattr(cfg, "strategy_targets", {"high_risk": 10.0, "stocks": 40.0, "safe": 50.0})
    w_div = float(getattr(cfg, "diversified_weight", 0.5))
    rows, total, equity = [], 0.0, 0.0
    exposure = {"high_risk": 0.0, "stocks": 0.0, "diversified": 0.0, "safe": 0.0, "unclassified": 0.0}
    missing, at_cost = [], []
    for h in hold:
        if h.is_cash:
            fx = fx_to_eur(market, cfg, h.currency)
            eur = h.quantity / fx if fx else None
            rows.append({"key": h.key, "name": h.name, "cash": True, "asset": "cash", "value_eur": eur,
                         "currency": h.currency})
            if eur is not None:
                total += eur
                exposure["safe"] += eur
            continue
        unit = 0.01 if h.asset == "bond" else 1.0
        sym, _ = resolve(h)
        q = market.quote(sym) if sym else None
        price = q.price if q else None
        cur = (q.currency if q and q.currency else h.currency)
        if price is None and h.asset == "bond":
            price, cur = h.avg_price, h.currency
            at_cost.append(h.name)
        fx = fx_to_eur(market, cfg, cur)
        eur = price * h.quantity * unit / fx if price is not None and fx else None
        if eur is None:
            missing.append(h.name)
        else:
            total += eur
            row_exposure = {"high_risk": 0.0, "stocks": 0.0, "diversified": 0.0, "safe": 0.0, "unclassified": 0.0}
            if h.isin and h.isin.upper() in high_risk_isins:
                row_exposure["high_risk"] = eur
            elif h.asset == "stock":
                row_exposure["stocks"] = eur
            elif h.asset == "bond":
                row_exposure["safe"] = eur
            elif h.asset == "fund":
                allocation = fund_allocations.get(h.key)
                if allocation:
                    row_exposure["diversified"] = eur * allocation.get("stock", 0.0)
                    row_exposure["safe"] = eur * (allocation.get("bond", 0.0) + allocation.get("cash", 0.0))
                    row_exposure["unclassified"] = eur * allocation.get("other", 0.0)
                else:
                    row_exposure["unclassified"] = eur
            else:
                row_exposure["unclassified"] = eur
            for bucket, value in row_exposure.items():
                exposure[bucket] += value
            equity += row_exposure["high_risk"] + row_exposure["stocks"] + row_exposure["diversified"]
        fx_cost = fx_to_eur(market, cfg, h.currency)
        cost_eur = h.avg_price * h.quantity * unit / fx_cost if fx_cost else None
        rows.append({"key": h.key, "name": h.name, "cash": False, "asset": h.asset, "symbol": sym,
                     "quantity": h.quantity, "price": price, "currency": cur, "value_eur": eur,
                     "cost_eur": cost_eur, "pnl_pct": (eur / cost_eur - 1) * 100 if eur and cost_eur else None,
                     "exposure_eur": row_exposure if eur is not None else None})
    unknown_allocation = [r["name"] for r in rows if r.get("asset") == "fund" and r.get("exposure_eur")
                          and r["exposure_eur"]["unclassified"] == r["value_eur"]
                          and r["key"] not in fund_allocations]
    def pct(value):
        return value / total * 100 if total else None
    # Funds' stock share counts at w_div in the stock quota; the raw figures are kept alongside it.
    actual_pct = {"high_risk": pct(exposure["high_risk"]),
                  "stocks": pct(exposure["stocks"] + w_div * exposure["diversified"]),
                  "safe": pct(exposure["safe"]),
                  "stocks_direct": pct(exposure["stocks"]),
                  "diversified": pct(exposure["diversified"])}
    strategy = {
        "targets_pct": dict(targets),
        "diversified_weight": w_div,
        "actual_pct": actual_pct,
        "drift_pct": {key: actual_pct[key] - target for key, target in targets.items()
                      if actual_pct.get(key) is not None},
        "unclassified_pct": exposure["unclassified"] / total * 100 if total else None,
        "unclassified_eur": exposure["unclassified"],
        "complete": exposure["unclassified"] < 0.01 and not missing,
    }
    return {"rows": rows, "total_eur": total if not missing else None, "known_total_eur": total,
            "stocks_eur": equity, "equity_share": equity / total * 100 if total else None,
            "asset_exposure_eur": exposure, "strategy": strategy,
            "unknown_allocation": unknown_allocation, "missing": missing, "at_cost": at_cost}
