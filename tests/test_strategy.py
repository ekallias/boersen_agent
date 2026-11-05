import tempfile
import unittest
from pathlib import Path

from boersen_agent import config
from boersen_agent.marketdata import EODHDProvider


class StrategyConfigTests(unittest.TestCase):
    def test_strategy_targets_and_high_risk_isins_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text(
                '[strategy]\ntargets = { high_risk = 20, stocks = 30, safe = 50 }\n'
                'high_risk_isins = ["us0000000001"]\n')
            cfg = config.load(path)
        self.assertEqual(cfg.strategy_targets, {"high_risk": 20.0, "stocks": 30.0, "safe": 50.0})
        self.assertEqual(cfg.high_risk_isins, ["US0000000001"])

    def test_strategy_targets_must_add_to_100(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text('[strategy]\ntargets = { high_risk = 10, stocks = 40, safe = 40 }\n')
            with self.assertRaisesRegex(ValueError, "add up to 100"):
                config.load(path)


class EODHDAllocationTests(unittest.TestCase):
    def test_asset_allocation_is_normalized_to_exposure_fractions(self):
        provider = EODHDProvider("test-token")
        request = {}

        def fake_get(path, **params):
            request.update(path=path, params=params)
            return {
            "ETF_Data": {"Asset_Allocation": {
                "Stock US": {"Net_Assets_%": 50},
                "Stock non-US": {"Net_Assets_%": 10},
                "Bond": {"Net_Assets_%": 20}, "Cash": {"Net_Assets_%": 10},
                "Other": {"Net_Assets_%": 10}}}}

        provider._get = fake_get
        self.assertEqual(provider.asset_allocation("FUND.US"),
                         {"stock": 0.6, "bond": 0.2, "cash": 0.1, "other": 0.1})
        self.assertEqual(request["path"], "v1.1/fundamentals/FUND.US")
        self.assertEqual(request["params"]["filter"], "ETF_Data::Asset_Allocation")

    def test_incomplete_or_unusable_allocation_is_not_inferred(self):
        provider = EODHDProvider("test-token")
        provider._get = lambda *_args, **_kwargs: {
            "ETF_Data": {"Asset_Allocation": {"Stock US": {"Net_Assets_%": 80}}}}
        self.assertIsNone(provider.asset_allocation("FUND.US"))


if __name__ == "__main__":
    unittest.main()
