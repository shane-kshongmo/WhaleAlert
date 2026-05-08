import unittest

from config import TokenConfig
from data.data_store import DataStore

try:
    from web import dashboard
except ModuleNotFoundError as exc:
    dashboard = None
    DASHBOARD_IMPORT_ERROR = exc
else:
    DASHBOARD_IMPORT_ERROR = None


class FakeTopCoins:
    def __init__(self, symbols):
        self._symbols = list(symbols)

    def get_top_coins(self):
        return list(self._symbols)


@unittest.skipIf(dashboard is None, f"dashboard dependencies unavailable: {DASHBOARD_IMPORT_ERROR}")
class DashboardApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_store = dashboard.store
        self.original_top_coins = dashboard._top_coins
        dashboard.store = DataStore(":memory:")

    def tearDown(self):
        dashboard.store = self.original_store
        dashboard._top_coins = self.original_top_coins

    async def test_tokens_endpoint_exposes_hybrid_metadata(self):
        dashboard.set_managers(None, None, top_coins=FakeTopCoins(["BTCUSDT", "DOGEUSDT"]))
        dashboard.store.save_snapshot({
            "symbol": "BTCUSDT",
            "price": 100000,
            "change_24h": 2.5,
            "control_score": 28,
            "phase": "趋势确认",
            "pump_probability": 22,
            "signals": [],
            "metrics": {},
        })
        dashboard.store.save_snapshot({
            "symbol": "PEPEUSDT",
            "price": 0.00001,
            "change_24h": 12.0,
            "control_score": 61,
            "phase": "吸筹末期",
            "pump_probability": 54,
            "signals": [],
            "metrics": {},
        })

        payload = await dashboard.get_all_tokens()
        by_symbol = {token["symbol"]: token for token in payload["tokens"]}

        btc = by_symbol["BTCUSDT"]
        self.assertEqual(btc["strategy"], "trend")
        self.assertEqual(btc["watch_source"], "top10")
        self.assertTrue(btc["is_top_coin"])
        self.assertTrue(btc["is_trend_coin"])
        self.assertTrue(btc["protected_from_auto_removal"])

        pepe = by_symbol["PEPEUSDT"]
        self.assertEqual(pepe["strategy"], "whale")
        self.assertEqual(pepe["watch_source"], "configured")
        self.assertTrue(pepe["is_configured_watch"])
        self.assertTrue(pepe["protected_from_auto_removal"])

    async def test_watchlist_endpoint_keeps_configured_plus_top10_and_dynamic_auto_split(self):
        dynamic_symbol = "ZZZAUTOUSDT"
        dashboard.WATCH_TOKENS.append(TokenConfig(dynamic_symbol, "ZZZAuto"))
        try:
            dashboard.set_managers(None, None, top_coins=FakeTopCoins(["DOGEUSDT", "BTCUSDT"]))
            payload = await dashboard.get_watchlist()
        finally:
            dashboard.WATCH_TOKENS.pop()

        by_symbol = {token["symbol"]: token for token in payload["watchlist"]}

        doge = by_symbol["DOGEUSDT"]
        self.assertEqual(doge["watch_source"], "configured+top10")
        self.assertEqual(doge["strategy"], "whale")
        self.assertTrue(doge["protected_from_auto_removal"])

        dynamic = by_symbol[dynamic_symbol]
        self.assertEqual(dynamic["watch_source"], "auto")
        self.assertFalse(dynamic["is_configured_watch"])
        self.assertFalse(dynamic["is_top_coin"])
        self.assertFalse(dynamic["protected_from_auto_removal"])


if __name__ == "__main__":
    unittest.main()
