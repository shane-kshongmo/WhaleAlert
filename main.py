"""
主调度器 (v4 - 并行化)

数据流:
  采集(10路并发) → 双向分析(控盘+出货同时) → 预警(涨跌并行) → 事件检测(并行) → 学习

运行: python main.py
"""
import time
import json
import asyncio
import logging
import signal
import sys
import threading
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from config import WATCH_TOKENS, SCAN_INTERVAL_MINUTES, KLINE_INTERVAL
from data.binance_client import BinanceClient
from data.realtime_engine import RealtimeEngine
from data.onchain_client import OnchainClient
from data.data_store import DataStore
from data.token_manager import TokenManager
from data.auto_discovery import AutoDiscoveryScanner
from analysis.indicators import calc_indicators
from analysis.ml_predictor import PredictionManager, extract_features
from analysis.whale_detector import WhaleDetector, WhaleAnalysis, CrashDetector, CrashAnalysis
from analysis.alert_engine import AlertEngine
from analysis.pump_monitor import PumpMonitor
from analysis.learning_engine import LearningEngine
from alerts.telegram_bot import TelegramBot
from alerts.webhook import WebhookNotifier
from alerts.logger import setup_logging
from trading.paper_trader import PaperTrader

logger = logging.getLogger(__name__)


class WhaleAlertService:

    def __init__(self):
        self.binance = BinanceClient()
        self.onchain = OnchainClient()
        self.store = DataStore()
        self.detector = WhaleDetector()
        self.crash_detector = CrashDetector()
        self.alert_engine = AlertEngine(self.store)
        self.pump_monitor = PumpMonitor(self.store)
        self.learning_engine = LearningEngine(self.store, self.pump_monitor)
        self.token_manager = TokenManager(self.store)
        self.scanner = AutoDiscoveryScanner(self.store)
        self.ml = PredictionManager(self.store, model_type="gbdt")
        self.telegram = TelegramBot()
        self.webhook = WebhookNotifier()
        self.realtime = RealtimeEngine()
        self.trader = PaperTrader(self.store)

        self._running = True
        self._scan_count = 0
        self._feature_cache: Dict = {}
        self._prev_prices: Dict[str, float] = {}  # for breakout detection

        self.learning_engine.restore_learned_thresholds()
        logger.info(f"[Init] 代币:{self.token_manager.get_token_count()} ML:{self.ml.predictor.is_ready()}")

    # ══════════════════════════════════════════════════════════════════
    # 主扫描
    # ══════════════════════════════════════════════════════════════════

    async def run_scan(self):
        self._scan_count += 1
        start = time.time()
        logger.info(f"{'='*60}")
        logger.info(f"[Scan #{self._scan_count}] {len(WATCH_TOKENS)} 代币")

        current_prices: Dict = {}

        # ── STEP 0: 自动发现 ──
        try:
            disc = await self.scanner.run_discovery()
            if not disc.get("skipped") and disc.get("added"):
                await self.telegram.send_message(
                    f"🔍 发现 {len(disc['added'])} 新代币 | "
                    f"{', '.join(disc['added'][:8])} | 当前{disc['total']}")
        except Exception as e:
            logger.error(f"[Discovery] {e}", exc_info=True)

        # ── STEP 0.5: 全量价格 ──
        try:
            current_prices = await self.scanner.get_all_price_changes()
        except Exception:
            pass

        token_list = list(WATCH_TOKENS)

        # ══════════════════════════════════════════════════════════════
        # STEP 1: 并发采集 + 双向分析
        #   每个代币: 1次API → WhaleAnalysis + CrashAnalysis
        # ══════════════════════════════════════════════════════════════
        sem = asyncio.Semaphore(5)

        async def analyze_token(tcfg) -> Tuple[Optional[WhaleAnalysis], Optional[CrashAnalysis]]:
            async with sem:
                try:
                    sym = tcfg.symbol
                    snap = await self.binance.collect_full_snapshot(sym)
                    klines = snap.get("klines", [])
                    if not klines or len(klines) < 30:
                        return None, None

                    ind = calc_indicators(klines, sym)
                    ob = snap.get("orderbook")
                    tr = snap.get("trades")
                    oc = None
                    if tcfg.contract and tcfg.chain in ("eth", "bsc", "arb"):
                        try:
                            oc = await self.onchain.analyze_token(
                                symbol=sym, contract=tcfg.contract,
                                chain=tcfg.chain, current_price=ind.price)
                        except Exception:
                            pass

                    self.store.cache_klines(sym, KLINE_INTERVAL, klines[-200:])
                    ob_r = ob.bid_ask_ratio if ob else 0.5
                    tr_b = tr.buy_ratio if tr else 0.5
                    ts = int(time.time() * 1000)

                    # 控盘 + 出货: 同一份数据, 零额外开销
                    funding = snap.get("funding_rate")

                    whale = self.detector.analyze(
                        symbol=sym, indicators=ind, onchain=oc,
                        orderbook_spread=ob.spread_pct if ob else 0,
                        orderbook_ratio=ob_r,
                        trade_stats_large_pct=tr.large_trade_pct if tr else 0,
                        trade_stats_buy_ratio=tr_b, timestamp=ts,
                        funding_rate=funding)

                    crash = self.crash_detector.analyze(
                        symbol=sym, indicators=ind, onchain=oc,
                        orderbook_ratio=ob_r, trade_buy_ratio=tr_b, timestamp=ts,
                        funding_rate=funding)

                    # ML校正
                    ml = self.ml.predict(ind, whale, oc)
                    if ml["source"] != "rule":
                        # Blend: use max of rule-based and ML probability
                        # Avoids ML model returning 0 and overriding valid rule-based estimate
                        rule_prob = whale.pump_probability
                        ml_prob = ml["final_prob"]
                        whale.pump_probability = max(rule_prob, ml_prob)
                    self._feature_cache[sym] = ml["features"]

                    # P0-B: 保存待标签ML样本 (24h后验证)
                    # 每次扫描保存样本, 标签将在24h后通过前向价格验证生成
                    try:
                        self.ml.save_ml_sample_pending(
                            symbol=sym,
                            features=ml["features"],
                            entry_price=whale.price,
                            timestamp=ts
                        )
                    except Exception as e:
                        logger.debug(f"[ML] Failed to save pending sample for {sym}: {e}")

                    # 存储
                    self.store.save_snapshot({
                        "symbol": sym, "timestamp": ts,
                        "price": whale.price, "volume_24h": whale.volume_24h,
                        "change_24h": whale.change_24h,
                        "control_score": whale.control_score, "phase": whale.phase,
                        "pump_probability": whale.pump_probability,
                        "signals": [s.to_dict() for s in whale.signals],
                        "metrics": whale.to_dict().get("dimensions", {}),
                    })
                    current_prices[sym] = {
                        "price": whale.price,
                        "change_1h": whale.change_24h * 0.15,
                        "change_4h": whale.change_24h * 0.4,
                        "change_24h": whale.change_24h,
                        "volume_current": whale.volume_24h,
                        "volume_avg": whale.volume_24h / 1.5,
                    }
                    return whale, crash
                except Exception as e:
                    logger.error(f"[Scan] {tcfg.symbol}: {e}", exc_info=True)
                    return None, None

        raw = await asyncio.gather(*[analyze_token(t) for t in token_list], return_exceptions=True)

        all_whales: List[WhaleAnalysis] = []
        all_crashes: List[CrashAnalysis] = []
        for r in raw:
            if isinstance(r, Exception):
                continue
            w, c = r if isinstance(r, tuple) else (None, None)
            if w:
                all_whales.append(w)
            if c and c.crash_score > 0:
                all_crashes.append(c)

        t1 = time.time() - start
        logger.info(f"[Step1] {t1:.1f}s | 控盘{len(all_whales)} 出货{len(all_crashes)}")

        # ══════════════════════════════════════════════════════════════
        # STEP 2: 预警 — 暴涨 + 暴跌 并行
        # ══════════════════════════════════════════════════════════════
        async def _pump_alerts():
            als = self.alert_engine.evaluate_batch(all_whales)
            for a in als:
                await self.telegram.send_alert(a.message)
                await self.webhook.send(a.message)
            return als

        async def _crash_alerts():
            sent = []
            decs = self.alert_engine.evaluate_crash_batch(all_crashes)
            for cd in decs:
                await self.telegram.send_message(cd.message)
                await self.webhook.send(cd.message)
                sent.append(cd)
                co = next((c for c in all_crashes if c.symbol == cd.symbol), None)
                if co:
                    self.pump_monitor.save_crash_alert(
                        cd.symbol, co.crash_score, co.phase, co.crash_probability,
                        json.dumps([s.to_dict() for s in co.signals]), cd.message)
            return sent

        pump_alerts, crash_alerts = await asyncio.gather(_pump_alerts(), _crash_alerts())
        if pump_alerts:
            logger.warning(f"🚨 {len(pump_alerts)} 暴涨预警")
        if crash_alerts:
            logger.warning(f"📉 {len(crash_alerts)} 暴跌预警")

        # ── STEP 2.5: Breakout Detection (突发拉盘) ──
        breakout_alerts = []
        if self._prev_prices:
            breakout_alerts = self.alert_engine.detect_breakouts(
                all_whales, self._prev_prices, current_prices)
            for ba in breakout_alerts:
                await self.telegram.send_alert(ba.message)
                await self.webhook.send(ba.message)
                # Open breakout position with tight stops
                cur = current_prices.get(ba.symbol, {})
                price = cur.get("price", 0) if isinstance(cur, dict) else cur
                if price > 0 and ba.trade_signal:
                    self.trader.open_position(
                        symbol=ba.symbol, direction="long", price=price,
                        alert_data={
                            "score": ba.analysis.control_score,
                            "phase": f"突发拉盘",
                            "probability": 0,
                            "signals": [],
                        },
                        sl_pct=ba.trade_signal.get("stop_loss_pct"),
                        tp_pct=ba.trade_signal.get("take_profit_pct"),
                        volume_24h=ba.analysis.volume_24h,
                        current_prices=current_prices)
        if breakout_alerts:
            logger.warning(f"⚡ {len(breakout_alerts)} 突发拉盘")

        # Save prices for next scan's breakout detection
        self._prev_prices = {}
        for sym, val in current_prices.items():
            p = val.get("price", 0) if isinstance(val, dict) else val
            if p > 0:
                self._prev_prices[sym] = p

        # ── Paper Trading: Open positions on alerts ──
        for a in pump_alerts:
            if hasattr(a, 'trade_signal') and a.trade_signal:
                self.trader.open_position(
                    symbol=a.symbol, direction="long",
                    price=a.analysis.price,
                    alert_data={
                        "score": a.analysis.control_score,
                        "phase": a.analysis.phase,
                        "probability": a.analysis.pump_probability,
                        "signals": [s.to_dict() for s in a.analysis.signals],
                    },
                    sl_pct=a.trade_signal.get("stop_loss_pct"),
                    tp_pct=a.trade_signal.get("take_profit_pct"),
                    volume_24h=a.analysis.volume_24h,
                    current_prices=current_prices)
        for cd in crash_alerts:
            if hasattr(cd, 'trade_signal') and cd.trade_signal:
                price = current_prices.get(cd.symbol, {}).get("price", 0)
                vol = current_prices.get(cd.symbol, {}).get("volume_current", 0)
                if price > 0:
                    crash_analysis = getattr(cd, "analysis", None)
                    self.trader.open_position(
                        symbol=cd.symbol, direction="short", price=price,
                        alert_data={
                            "score": crash_analysis.crash_score if crash_analysis else 0,
                            "phase": crash_analysis.phase if crash_analysis else "",
                            "crash_probability": crash_analysis.crash_probability if crash_analysis else 0,
                            "signals": [s.to_dict() for s in crash_analysis.signals] if crash_analysis else [],
                        },
                        sl_pct=cd.trade_signal.get("stop_loss_pct"),
                        tp_pct=cd.trade_signal.get("take_profit_pct"),
                        volume_24h=vol,
                        current_prices=current_prices)

        # ══════════════════════════════════════════════════════════════
        # STEP 3: 事件检测 — 暴涨/暴跌/误报 并行
        # ══════════════════════════════════════════════════════════════
        btc = current_prices.get("BTCUSDT", {}).get("change_24h", 0)

        async def _det_pump():
            evs = self.pump_monitor.check_for_pumps(current_prices, btc)
            for e in evs:
                m = self._msg_pump(e)
                await self.telegram.send_message(m)
                await self.webhook.send(m)
            return evs

        async def _det_crash():
            evs = self.pump_monitor.check_for_crashes(current_prices, btc)
            for e in evs:
                m = self._msg_crash(e)
                await self.telegram.send_message(m)
                await self.webhook.send(m)
            return evs

        async def _det_fp():
            fps = self.pump_monitor.verify_past_alerts(current_prices)
            for fp in fps:
                await self.telegram.send_message(self._msg_fp(fp))
            return fps

        pumps, crashes, fps = await asyncio.gather(_det_pump(), _det_crash(), _det_fp())

        # Adaptive threshold feedback
        for e in pumps:
            if e.was_predicted:
                self.alert_engine.adaptive.record_hit(e.symbol)
        for fp in fps:
            self.alert_engine.adaptive.record_false_positive(fp["symbol"])

        # ── Paper Trading: Check open positions (with score deterioration) ──
        latest_scores = {w.symbol: w.control_score for w in all_whales}
        closed_trades = self.trader.check_positions(current_prices, latest_scores)

        # ══════════════════════════════════════════════════════════════
        # STEP 4: ML样本 + 学习 + 重训练
        # ══════════════════════════════════════════════════════════════
        ps = {e.symbol for e in pumps}
        for e in pumps:
            f = self._feature_cache.get(e.symbol)
            if f is not None:
                self.ml.record_sample(e.symbol, f, 1, e.pump_pct, 5.0)
        for fp in fps:
            f = self._feature_cache.get(fp.get("symbol", ""))
            if f is not None:
                self.ml.record_sample(fp["symbol"], f, 0, fp.get("actual_change_24h", 0), 3.0)
        for a in all_whales:
            if a.symbol not in ps:
                f = self._feature_cache.get(a.symbol)
                if f is not None:
                    self.ml.record_sample(a.symbol, f, 0, a.change_24h, 1.0)

        if self._scan_count % 6 == 0:
            try:
                lesson = self.learning_engine.run_learning_cycle(days=14)
                if lesson:
                    m = self._msg_learn(lesson)
                    await self.telegram.send_message(m)
                    await self.webhook.send(m)
            except Exception as e:
                logger.error(f"[Learning] {e}", exc_info=True)

        if self._scan_count % 12 == 0:
            try:
                r = self.ml.maybe_retrain()
                if r and r.get("trained"):
                    await self.telegram.send_message(
                        f"🤖 ML v{self.ml.predictor._model_version} "
                        f"AUC={r.get('auc',0):.3f} P={r.get('precision',0):.1%} "
                        f"R={r.get('recall',0):.1%}")
            except Exception as e:
                logger.error(f"[ML] {e}", exc_info=True)

        # P0-B: Process pending ML samples (every 6 scans = ~90 minutes)
        # 为待标签样本生成真实标签 (前向价格验证)
        if self._scan_count % 6 == 0:
            try:
                labeled_count = self.ml.label_pending_samples(older_than_hours=24)
                if labeled_count > 0:
                    logger.info(f"[ML] Labeled {labeled_count} pending samples via 24h forward verification")
            except Exception as e:
                logger.error(f"[ML] Failed to label pending samples: {e}", exc_info=True)

        now = datetime.now()
        if now.hour == 8 and now.minute < SCAN_INTERVAL_MINUTES + 1:
            await self.telegram.send_message(
                self.alert_engine.generate_daily_summary(all_whales, self.pump_monitor))
            await self.telegram.send_message(
                self.learning_engine.get_status_report(30))
            trade_stats = self.trader.get_stats(30)
            await self.telegram.send_message(
                self.trader.format_stats_message(trade_stats))

        elapsed = time.time() - start
        logger.info(
            f"[Scan #{self._scan_count}] {elapsed:.1f}s (采集{t1:.1f}s) | "
            f"分析{len(all_whales)} 涨警{len(pump_alerts)} 跌警{len(crash_alerts)} "
            f"爆涨{len(pumps)} 暴跌{len(crashes)} 误报{len(fps)}")

    # ══════════════════════════════════════════════════════════════════
    # 快速信号 (实时引擎)
    # ══════════════════════════════════════════════════════════════════

    async def check_fast_signals(self):
        """检查实时引擎产生的快速信号, 对命中代币执行快速分析并推送预警"""
        signals = self.realtime.get_pending_fast_signals()
        if not signals:
            return

        # 按代币去重 (同代币多个信号只分析一次)
        seen_symbols = set()
        for sig in signals:
            symbol = sig.get("symbol", "")
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            logger.info(f"[FastSignal] {symbol} type={sig.get('type')} — 触发快速分析")

            # 找到对应 TokenConfig
            tcfg = next((t for t in WATCH_TOKENS if t.symbol == symbol), None)
            if tcfg is None:
                continue

            try:
                # 复用 analyze_token 逻辑: 采集快照 + 双向分析
                from analysis.indicators import calc_indicators
                snap = await self.binance.collect_full_snapshot(symbol)
                klines = snap.get("klines", [])
                if not klines or len(klines) < 30:
                    continue

                ind = calc_indicators(klines, symbol)
                ob = snap.get("orderbook")
                tr = snap.get("trades")
                ob_r = ob.bid_ask_ratio if ob else 0.5
                tr_b = tr.buy_ratio if tr else 0.5
                ts = int(time.time() * 1000)

                whale = self.detector.analyze(
                    symbol=symbol, indicators=ind, onchain=None,
                    orderbook_spread=ob.spread_pct if ob else 0,
                    orderbook_ratio=ob_r,
                    trade_stats_large_pct=tr.large_trade_pct if tr else 0,
                    trade_stats_buy_ratio=tr_b, timestamp=ts)

                crash = self.crash_detector.analyze(
                    symbol=symbol, indicators=ind, onchain=None,
                    orderbook_ratio=ob_r, trade_buy_ratio=tr_b, timestamp=ts)

                # 附加实时指标到信号消息
                rt = self.realtime.get_realtime_metrics(symbol)
                signal_info = (
                    f"[实时信号] {symbol} | {sig.get('type')}\n"
                    f"  量涌1m={rt['volume_surge_1m']:.1f}x 5m={rt['volume_surge_5m']:.1f}x\n"
                    f"  价格5m={rt['price_change_5m']:+.2f}% 15m={rt['price_change_15m']:+.2f}%\n"
                    f"  大单5m={rt['large_trade_count_5m']} 买卖比={rt['bid_ask_imbalance']:.2f}"
                )

                # 评估预警
                pump_alerts = self.alert_engine.evaluate_batch([whale])
                for a in pump_alerts:
                    await self.telegram.send_alert(a.message + f"\n{signal_info}")
                    await self.webhook.send(a.message)

                crash_alerts = self.alert_engine.evaluate_crash_batch([crash] if crash.crash_score > 0 else [])
                for cd in crash_alerts:
                    await self.telegram.send_message(cd.message + f"\n{signal_info}")
                    await self.webhook.send(cd.message)

            except Exception as e:
                logger.error(f"[FastSignal] {symbol} 分析失败: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════════════
    # 消息
    # ══════════════════════════════════════════════════════════════════

    def _msg_pump(self, ev) -> str:
        h = "✅命中" if ev.was_predicted else "❌漏报"
        return (f"🚀 爆涨 {ev.symbol} +{ev.pump_pct:.1f}%/{ev.pump_duration_hours}h\n"
                f"${ev.pump_start_price:,.6g}→${ev.pump_peak_price:,.6g} 量{ev.volume_surge_ratio:.1f}x\n"
                f"{h} 评分{ev.pre_pump_score}")

    def _msg_crash(self, ev) -> str:
        h = "✅命中" if ev.was_predicted else "❌漏报"
        return (f"📉 暴跌 {ev.symbol} {ev.crash_pct:+.1f}%/{ev.crash_duration_hours}h\n"
                f"${ev.crash_start_price:,.6g}→${ev.crash_bottom_price:,.6g} 量{ev.volume_surge_ratio:.1f}x\n"
                f"{h} 评分{ev.pre_crash_score}")

    def _msg_learn(self, lesson) -> str:
        adjs = " | ".join(f"{a['param']}:{a['old']}→{a['new']}" for a in lesson.adjustments[:3])
        return f"📚 自学习 {lesson.missed_count}漏报 → {adjs or '无调整'}"

    def _msg_fp(self, fp) -> str:
        return (f"🔕 误报 {fp['symbol']} 评分{fp.get('alert_score',0)} "
                f"实际{fp.get('actual_change_24h',0):+.1f}% ⚠️已纳入学习")

    # ══════════════════════════════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════════════════════════════

    async def run_loop(self):
        logger.info(
            f"🐋 Whale Alert v4 启动\n"
            f"  📡 {self.token_manager.get_token_count()}代币 ⏱{SCAN_INTERVAL_MINUTES}min\n"
            f"  🚀 涨≥30%/24h 📉 跌≥50%/4h 🔄 并行执行")

        # 启动实时引擎
        symbols = [t.symbol for t in WATCH_TOKENS]
        await self.realtime.start(symbols=symbols)

        await self.run_scan()

        scan_interval_secs = SCAN_INTERVAL_MINUTES * 60
        fast_signal_interval_secs = 60
        elapsed_since_scan = 0

        while self._running:
            try:
                await asyncio.sleep(fast_signal_interval_secs)
                elapsed_since_scan += fast_signal_interval_secs

                if self._running:
                    await self.check_fast_signals()

                if elapsed_since_scan >= scan_interval_secs and self._running:
                    elapsed_since_scan = 0
                    await self.run_scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Loop] {e}", exc_info=True)
                await asyncio.sleep(60)
                elapsed_since_scan += 60

        await self.realtime.stop()
        await self.binance.close()
        await self.onchain.close()

    def stop(self):
        self._running = False


def main():
    setup_logging()
    service = WhaleAlertService()
    signal.signal(signal.SIGINT, lambda *_: service.stop())
    signal.signal(signal.SIGTERM, lambda *_: service.stop())

    from web.dashboard import start_dashboard, set_managers
    set_managers(service.token_manager, service.pump_monitor, service.scanner, service.trader)
    threading.Thread(target=start_dashboard, daemon=True).start()
    logger.info(f"📊 Dashboard: http://localhost:8888")

    asyncio.run(service.run_loop())


if __name__ == "__main__":
    main()
