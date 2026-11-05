# Personal investment strategy

The `[strategy]` section of `config.toml` is the editable source of portfolio targets. The example
uses 10% high-risk / 40% stocks / 50% safe as a starting point only; it is not investment advice.
The targets must add up to 100%.

```toml
[strategy]
targets = { high_risk = 10, stocks = 40, safe = 50 }
high_risk_isins = []
```

## How portfolio exposure is measured

- **High risk:** the full market value of any holding whose ISIN is listed in `high_risk_isins`.
- **Stocks:** other directly held stocks, plus the stock portion of funds when the data provider
  supplies an asset-allocation breakdown.
- **Safe:** cash, bonds, and the cash/bond portion of funds with a known breakdown.
- **Unclassified:** any other fund exposure, or the full value of a fund whose composition is not
  available. It is not silently counted as stock or safe.

Fund composition is refreshed from EODHD ETF fundamentals when `provider = "eodhd"` and the account
has access to that data. Yahoo provides fund prices but not this asset-class breakdown. An unavailable
allocation is shown as a data gap and excluded from known equity exposure and the target buckets.

## Relationship to Börsensignale

The Börsensignale rule engine remains the baseline for market regime and security-level signals.
These personal targets are an additional portfolio-level check: the reports and AI fact pack compare
the current measured allocation with each target and show the percentage-point drift. A target
deviation does not override a stated newsletter rule or trigger a trade; the agent is decision support
only and never trades.

Review the targets and the list of high-risk ISINs as your strategy changes. Keep this file and the
matching `[strategy]` settings in `config.toml` aligned when documenting a change.
