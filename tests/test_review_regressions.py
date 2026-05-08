import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from config import TokenConfig, WATCH_TOKENS
from data.auto_discovery import AutoDiscoveryScanner
from data.data_store import DataStore
from trading.paper_trader import PaperTrader


class AutoDiscoveryRegressionTests(unittest.TestCase):
    def test_manual_symbols_keep_configured_watchlist_but_not_dynamic_additions(self):
        scanner = AutoDiscoveryScanner(DataStore(":memory:"))
        dynamic_symbol = "ZZZTESTUSDT"
        WATCH_TOKENS.append(TokenConfig(dynamic_symbol, "ZZZTEST"))
        try:
            manual_symbols = scanner._get_manual_symbols()
        finally:
            WATCH_TOKENS.pop()

        self.assertIn("PEPEUSDT", manual_symbols)
        self.assertIn("BTCUSDT", manual_symbols)
        self.assertNotIn(dynamic_symbol, manual_symbols)


class PaperTraderRegressionTests(unittest.TestCase):
    def test_mainstream_symbols_bypass_legacy_min_alert_score_gate(self):
        trader = PaperTrader(DataStore(":memory:"))

        trade = trader.open_position(
            symbol="BTCUSDT",
            direction="long",
            price=100_000,
            alert_data={"score": 30, "probability": 25, "signals": []},
            volume_24h=1_000_000,
            current_prices={},
        )

        self.assertIsNotNone(trade)
        self.assertEqual(trade.signal_tier, "mainstream")

    def test_non_mainstream_symbols_still_respect_min_alert_score_gate(self):
        trader = PaperTrader(DataStore(":memory:"))

        trade = trader.open_position(
            symbol="PEPEUSDT",
            direction="long",
            price=0.00001,
            alert_data={"score": 30, "probability": 25, "signals": []},
            volume_24h=1_000_000,
            current_prices={},
        )

        self.assertIsNone(trade)


class ServiceRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_signals_pass_strategy_and_indicators_to_alert_engine(self):
        indicator = SimpleNamespace(
            ema20=1,
            ema50=1,
            ema200=1,
            rsi_14=55,
            vol_spike_ratio=2.0,
            macd_histogram=1,
            macd=1,
        )

        class FakeRealtime:
            def get_pending_fast_signals(self):
                return [{"symbol": "BTCUSDT", "type": "volume_surge_5m"}]

            def get_realtime_metrics(self, symbol):
                return {
                    "volume_surge_1m": 1.0,
                    "volume_surge_5m": 3.5,
                    "price_change_5m": 1.2,
                    "price_change_15m": 2.3,
                    "large_trade_count_5m": 7,
                    "bid_ask_imbalance": 0.61,
                }

        class FakeBinance:
            async def collect_full_snapshot(self, symbol):
                return {
                    "klines": [object()] * 30,
                    "orderbook": SimpleNamespace(bid_ask_ratio=0.6, spread_pct=0.1),
                    "trades": SimpleNamespace(large_trade_pct=25.0, buy_ratio=0.55),
                }

        class FakeDetector:
            def analyze(self, **kwargs):
                return SimpleNamespace(
                    symbol=kwargs["symbol"],
                    control_score=30,
                    phase="趋势确认",
                    pump_probability=25,
                    timestamp=123,
                    signals=[],
                    price=100_000,
                    change_24h=2.0,
                    volume_24h=5_000_000,
                )

        class FakeCrashDetector:
            def analyze(self, **kwargs):
                return SimpleNamespace(crash_score=0)

        class CapturingAlertEngine:
            def __init__(self):
                self.strategy_map = None
                self.indicators_map = None

            def evaluate_batch(self, analyses, strategy_map=None, indicators_map=None):
                self.strategy_map = strategy_map
                self.indicators_map = indicators_map
                return []

            def evaluate_crash_batch(self, analyses):
                return []

        class FakeNotifier:
            async def send_alert(self, message):
                return None

            async def send_message(self, message):
                return None

            async def send(self, message):
                return None

        service = main.WhaleAlertService.__new__(main.WhaleAlertService)
        service.realtime = FakeRealtime()
        service.binance = FakeBinance()
        service.detector = FakeDetector()
        service.crash_detector = FakeCrashDetector()
        service.alert_engine = CapturingAlertEngine()
        service.telegram = FakeNotifier()
        service.webhook = FakeNotifier()
        service.top_coins = SimpleNamespace(get_coin_strategy=lambda symbol: "trend")
        service._strategy_cache = {}
        service._indicators_cache = {}

        with patch.object(main, "WATCH_TOKENS", [TokenConfig("BTCUSDT", "Bitcoin")]):
            with patch("analysis.indicators.calc_indicators", return_value=indicator):
                await service.check_fast_signals()

        self.assertEqual(service.alert_engine.strategy_map, {"BTCUSDT": "trend"})
        self.assertIs(service.alert_engine.indicators_map["BTCUSDT"], indicator)
        self.assertEqual(service._strategy_cache["BTCUSDT"], "trend")
        self.assertIs(service._indicators_cache["BTCUSDT"], indicator)

    async def test_refresh_top_coins_subscribes_new_realtime_symbols(self):
        subscribed = []

        class FakeRealtime:
            async def subscribe(self, symbols):
                subscribed.extend(symbols)

        class FakeTopCoins:
            async def refresh(self):
                return ["BTCUSDT", "NEWTOPUSDT"]

            def to_token_configs(self):
                return [
                    TokenConfig("BTCUSDT", "Bitcoin"),
                    TokenConfig("NEWTOPUSDT", "NewTop"),
                ]

        service = main.WhaleAlertService.__new__(main.WhaleAlertService)
        service.realtime = FakeRealtime()
        service.top_coins = FakeTopCoins()
        service._top_coins_last_refresh = 0

        with patch.object(main, "WATCH_TOKENS", [TokenConfig("PEPEUSDT", "Pepe")]):
            refreshed = await service._refresh_top_coins_watchlist()
            watch_symbols = [token.symbol for token in main.WATCH_TOKENS]

        self.assertEqual(refreshed, ["BTCUSDT", "NEWTOPUSDT"])
        self.assertEqual(subscribed, ["BTCUSDT", "NEWTOPUSDT"])
        self.assertIn("BTCUSDT", watch_symbols)
        self.assertIn("NEWTOPUSDT", watch_symbols)


if __name__ == "__main__":
    unittest.main()
