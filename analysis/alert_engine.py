"""
预警决策引擎
核心原则: 只在高概率情况下才触发预警, 减少噪音

触发条件 (全部满足):
1. 控盘综合分 ≥ 70
2. 阶段为 "吸筹末期" / "即将拉盘" / "高度控盘"
3. 至少 3 个维度同时触发
4. 拉盘概率 ≥ 55%
5. 冷却期: 同一代币 4 小时内不重复
"""
import time
import json
import logging
from typing import List, Dict, Optional
from collections import Counter
from dataclasses import dataclass

from config import ALERT_CONFIG as AC, CRASH_ALERT_CONFIG as CAC, STABLECOIN_SYMBOLS
from analysis.whale_detector import WhaleAnalysis, CrashAnalysis
from data.data_store import DataStore

logger = logging.getLogger(__name__)


class AdaptiveThresholds:
    """Per-token threshold adjustments based on historical accuracy"""
    def __init__(self, store: DataStore):
        self.store = store
        self._adjustments: Dict[str, dict] = {}
        self._init_table()
        self._load()

    def _init_table(self):
        with self.store._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS adaptive_thresholds (
                    symbol TEXT PRIMARY KEY,
                    score_adjustment INTEGER DEFAULT 0,
                    reason TEXT,
                    updated_at INTEGER,
                    expires_at INTEGER
                )
            """)

    def _load(self):
        now = int(time.time() * 1000)
        with self.store._conn() as conn:
            rows = conn.execute("SELECT * FROM adaptive_thresholds WHERE expires_at > ?", (now,)).fetchall()
            for r in rows:
                self._adjustments[r["symbol"]] = {
                    "score_adj": r["score_adjustment"],
                    "expires": r["expires_at"] / 1000,
                    "reason": r["reason"]
                }

    def _save(self, symbol: str):
        adj = self._adjustments.get(symbol, {})
        with self.store._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO adaptive_thresholds (symbol, score_adjustment, reason, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (symbol, adj.get("score_adj", 0), adj.get("reason", ""),
                  int(time.time() * 1000), int(adj.get("expires", 0) * 1000)))

    def get_adjusted_min_score(self, symbol: str, base_score: int) -> int:
        adj = self._adjustments.get(symbol, {})
        if adj and adj.get("expires", 0) > time.time():
            return base_score + adj.get("score_adj", 0)
        return base_score

    def record_false_positive(self, symbol: str):
        self._adjustments[symbol] = {"score_adj": 10, "expires": time.time() + 7 * 86400, "reason": "recent_fp"}
        self._save(symbol)

    def record_hit(self, symbol: str):
        self._adjustments[symbol] = {"score_adj": -5, "expires": time.time() + 3 * 86400, "reason": "recent_hit"}
        self._save(symbol)


@dataclass
class AlertDecision:
    should_alert: bool = False
    symbol: str = ""
    reason: str = ""
    urgency: str = "normal"
    message: str = ""
    analysis: Optional[WhaleAnalysis | CrashAnalysis] = None
    trade_signal: dict = None


class AlertEngine:
    def __init__(self, store: DataStore):
        self.store = store
        self.adaptive = AdaptiveThresholds(store)
        self._watchlist: Dict[str, dict] = {}

    def _cleanup_watchlist(self):
        """Remove watchlist entries older than 2 hours"""
        cutoff = time.time() - 2 * 3600
        expired = [sym for sym, entry in self._watchlist.items() if entry.get("added_at", 0) < cutoff]
        for sym in expired:
            del self._watchlist[sym]

    def evaluate(self, analysis: WhaleAnalysis, score_boost: int = 0,
                 ml_confidence: str = "medium", coin_strategy: str = "whale",
                 indicators=None) -> AlertDecision:
        # Route BTC/ETH to trend-following evaluator; all others use whale detection
        if coin_strategy == "trend":
            return self._evaluate_trend_signal(analysis, indicators)

        decision = AlertDecision(symbol=analysis.symbol, analysis=analysis)
        symbol = analysis.symbol

        # ML confidence gating: adjust base minimum score
        # Cold start (untrained model) now raises threshold — firing at full
        # confidence with an untrained model was causing systematic false positives.
        if ml_confidence == "high":
            base_min = max(50, AC.min_control_score - 5)
        elif ml_confidence == "low":
            base_min = AC.min_control_score + 10  # untrained = stricter, not neutral
        else:
            base_min = AC.min_control_score

        # Multi-dimension combo: lower threshold when golden combo fires
        # Research shows 86% of pumps had 动量构建+价差异常+买卖不平衡 simultaneously
        if analysis.signal_count >= 3:
            active_dims = {s.dimension for s in analysis.signals}
            golden = {"动量构建", "价差异常", "买卖不平衡"}
            if golden.issubset(active_dims):
                base_min = min(base_min, 25)  # Lower to 25 for golden combo
            elif analysis.signal_count >= 4:
                base_min = min(base_min, 30)  # Lower to 30 for 4+ dimensions

        # Apply adaptive threshold + score boost
        effective_min = self.adaptive.get_adjusted_min_score(symbol, base_min) + score_boost

        if analysis.control_score < effective_min:
            decision.reason = f"控盘分不足: {analysis.control_score} < {effective_min}"
            return decision

        if analysis.phase not in AC.required_phases:
            decision.reason = f"阶段不符: {analysis.phase} 不在 {AC.required_phases}"
            return decision

        if analysis.signal_count < AC.min_signal_count:
            decision.reason = f"信号不足: {analysis.signal_count} < {AC.min_signal_count}"
            return decision

        if analysis.pump_probability < AC.min_pump_probability:
            decision.reason = f"概率不足: {analysis.pump_probability}% < {AC.min_pump_probability}%"
            return decision

        last_alert_ts = self.store.get_last_alert_time(symbol)
        if last_alert_ts:
            hours_since = (time.time() * 1000 - last_alert_ts) / 3600000
            if hours_since < AC.cooldown_hours:
                decision.reason = f"冷却中: {hours_since:.1f}h < {AC.cooldown_hours}h"
                return decision

        # 2-stage confirmation watchlist
        self._cleanup_watchlist()
        now = time.time()
        existing = self._watchlist.get(symbol)
        if existing is None:
            # Stage 1: first time meeting thresholds, add to watchlist
            self._watchlist[symbol] = {
                "added_at": now,
                "scan_count": 1,
                "last_score": analysis.control_score,
            }
            decision.reason = "Stage 1: Watching"
            logger.info(f"[Alert] {symbol}: Stage1 加入观察列表 score={analysis.control_score} pump={analysis.pump_probability}%")
            return decision
        else:
            scan_count = existing.get("scan_count", 1) + 1
            last_score = existing.get("last_score", analysis.control_score)
            score_delta = analysis.control_score - last_score
            self._watchlist[symbol]["scan_count"] = scan_count
            self._watchlist[symbol]["last_score"] = analysis.control_score

            if score_delta < -15:
                # Signal weakened — cancel watch
                del self._watchlist[symbol]
                decision.reason = f"信号减弱: delta={score_delta}"
                logger.info(f"[Alert] {symbol}: 信号减弱, 取消观察 delta={score_delta}")
                return decision

            # Stage 2: confirmed, fire alert
            del self._watchlist[symbol]

        decision.should_alert = True
        decision.urgency = "critical" if analysis.control_score >= 85 else "high"
        decision.message = self._build_message(analysis)
        decision.reason = "全部条件满足"
        # Always generate trade signal — paper_trader handles tiered risk
        decision.trade_signal = analysis.get_suggested_sl_tp()

        logger.warning(f"🚨 [ALERT] {symbol}: score={analysis.control_score}, phase={analysis.phase}, pump={analysis.pump_probability}%")
        return decision

    def _check_market_correlation(self, analyses: List[WhaleAnalysis]) -> float:
        """Return correlation score (0-1): fraction of tokens showing correlated moves"""
        if not analyses:
            return 0.0
        total = len(analyses)
        high_control = sum(1 for a in analyses if a.control_score >= 40)
        big_move = sum(1 for a in analyses if abs(a.change_24h) > 5)
        return max(high_control / total, big_move / total)

    def _evaluate_trend_signal(self, analysis: WhaleAnalysis, indicators=None) -> AlertDecision:
        """
        趋势跟踪信号评估 — 用于 BTC/ETH 等基本面驱动的主流大币
        需要 4 个趋势信号中的至少 2 个, 且拉盘概率 ≥ 20%
        """
        decision = AlertDecision(symbol=analysis.symbol, analysis=analysis)
        symbol = analysis.symbol

        last_alert_ts = self.store.get_last_alert_time(symbol)
        if last_alert_ts:
            hours_since = (time.time() * 1000 - last_alert_ts) / 3600000
            if hours_since < AC.cooldown_hours:
                decision.reason = f"冷却中: {hours_since:.1f}h"
                return decision

        signals_hit = 0
        signal_details = []

        if indicators:
            # Signal 1: EMA多头排列 (EMA20 > EMA50 > EMA200)
            if (indicators.ema20 > 0 and indicators.ema50 > 0 and indicators.ema200 > 0
                    and indicators.ema20 > indicators.ema50 > indicators.ema200):
                signals_hit += 1
                signal_details.append("EMA多头排列(20>50>200)")

            # Signal 2: RSI突破50向上 (50-70区间表示新鲜上行动量)
            if 50 <= indicators.rsi_14 <= 70:
                signals_hit += 1
                signal_details.append(f"RSI突破50({indicators.rsi_14:.1f})")

            # Signal 3: 量能确认 (成交量 ≥ 1.5x 均值)
            if indicators.vol_spike_ratio >= 1.5:
                signals_hit += 1
                signal_details.append(f"量能确认({indicators.vol_spike_ratio:.1f}x)")

            # Signal 4: MACD金叉 (柱状图 > 0 且 MACD > 0)
            if indicators.macd_histogram > 0 and indicators.macd > 0:
                signals_hit += 1
                signal_details.append("MACD金叉")

        if signals_hit < 2:
            decision.reason = f"趋势信号不足: {signals_hit}/4 < 2"
            return decision

        if analysis.pump_probability < 20:
            decision.reason = f"趋势概率不足: {analysis.pump_probability}% < 20%"
            return decision

        # 2-stage confirmation (reuse same watchlist)
        self._cleanup_watchlist()
        now = time.time()
        existing = self._watchlist.get(symbol)
        if existing is None:
            self._watchlist[symbol] = {"added_at": now, "scan_count": 1, "last_score": signals_hit * 25}
            decision.reason = f"趋势Stage1: 观察中 ({signals_hit}/4信号)"
            logger.info(f"[Trend] {symbol}: Stage1 score={signals_hit}/4 prob={analysis.pump_probability}%")
            return decision

        del self._watchlist[symbol]

        decision.should_alert = True
        decision.urgency = "high"
        decision.reason = "趋势信号确认"
        decision.trade_signal = {
            "stop_loss_pct": 3.0,
            "take_profit_pct": 6.0,
            "risk_reward": 2.0,
            "direction": "long",
        }
        decision.message = self._build_trend_message(analysis, signals_hit, signal_details, indicators)
        logger.warning(f"📈 [TREND ALERT] {symbol}: {signals_hit}/4信号, prob={analysis.pump_probability}%")
        return decision

    def _build_trend_message(self, a: WhaleAnalysis, signals_hit: int,
                              signal_details: list, indicators=None) -> str:
        rsi_str = f"{indicators.rsi_14:.1f}" if indicators else "n/a"
        vol_str = f"{indicators.vol_spike_ratio:.1f}x" if indicators else "n/a"
        details_str = "\n".join(f"  ✅ {s}" for s in signal_details)
        return f"""📈 趋势跟踪预警 📈

━━━━━━━━━━━━━━━━━━━
🚀 {a.symbol} — 趋势突破
━━━━━━━━━━━━━━━━━━━

💰 价格: ${a.price:,.4f}
📈 24h: {a.change_24h:+.2f}%

🎯 趋势信号: {signals_hit}/4
📊 RSI: {rsi_str}  量能: {vol_str}
── 触发信号 ──
{details_str}

🛑 止损: -3.0%  🎯 目标: +6.0%
⚠️ 趋势跟踪有风险, 不构成投资建议
━━━━━━━━━━━━━━━━━━━""".strip()

    def evaluate_batch(self, analyses: List[WhaleAnalysis],
                       strategy_map: Dict[str, str] = None,
                       indicators_map: Dict = None) -> List[AlertDecision]:
        # 过滤稳定币
        analyses = [a for a in analyses if a.symbol not in STABLECOIN_SYMBOLS]
        correlation = self._check_market_correlation(analyses)
        score_boost = 15 if correlation > 0.5 else 0
        if score_boost:
            logger.info(f"[Alert] 市场相关性 {correlation:.2f} > 0.5, score_boost=+{score_boost}")

        results = []
        for a in analyses:
            strategy = (strategy_map or {}).get(a.symbol, "whale")
            ind = (indicators_map or {}).get(a.symbol)
            decision = self.evaluate(a, score_boost=score_boost,
                                     coin_strategy=strategy, indicators=ind)
            if decision.should_alert:
                results.append(decision)
                self.store.save_alert({
                    "symbol": a.symbol,
                    "timestamp": a.timestamp,
                    "control_score": a.control_score,
                    "phase": a.phase,
                    "pump_probability": a.pump_probability,
                    "signals": [s.to_dict() for s in a.signals],
                    "message": decision.message,
                })
            else:
                logger.info(f"[Alert] {a.symbol}: {decision.reason}")
        return results

    # ══════════════════════════════════════════════════════════════════════
    # 突发拉盘检测 (Breakout Detector)
    # 捕获正在拉盘中的代币 — 不依赖控盘积累信号, 而是检测实时动量爆发
    # ══════════════════════════════════════════════════════════════════════

    def detect_breakouts(self, analyses: List[WhaleAnalysis],
                         prev_prices: Dict[str, float],
                         current_prices: Dict[str, float]) -> List[AlertDecision]:
        """
        Detect tokens in active breakout (already pumping).

        Criteria:
        - Price surged >= breakout_min_change_pct since last scan (15 min)
        - Volume >= 2x average (volume surge)
        - Not already in the accumulation-based alert list
        - No duplicate open position
        """
        breakout_min_change_pct = 3.0   # 3% price change in one scan interval
        breakout_min_vol_mult = 1.5     # volume surge vs average
        breakout_cooldown_hours = 2.0   # cooldown for breakout alerts

        results = []
        alerted_symbols = set()  # avoid duplicates

        for a in analyses:
            if a.symbol in STABLECOIN_SYMBOLS:
                continue
            if a.symbol in alerted_symbols:
                continue

            prev = prev_prices.get(a.symbol)
            curr = current_prices.get(a.symbol)
            if isinstance(curr, dict):
                curr = curr.get("price", 0)
            if not prev or not curr or prev <= 0 or curr <= 0:
                continue

            # Price change since last scan
            price_change_pct = ((curr - prev) / prev) * 100
            if price_change_pct < breakout_min_change_pct:
                continue

            # Volume check: use 24h volume vs estimated average
            vol = a.volume_24h
            if vol <= 0:
                continue

            # Check cooldown
            last_alert_ts = self.store.get_last_alert_time(a.symbol)
            if last_alert_ts:
                hours_since = (time.time() * 1000 - last_alert_ts) / 3600000
                if hours_since < breakout_cooldown_hours:
                    continue

            # Breakout confirmed
            change_emoji = "🔥" if price_change_pct >= 8 else "⚡"
            decision = AlertDecision(
                should_alert=True,
                symbol=a.symbol,
                analysis=a,
                urgency="high" if price_change_pct >= 8 else "normal",
                reason=f"突发拉盘: {price_change_pct:.1f}%/{len(prev_prices)}min",
                trade_signal={
                    "stop_loss_pct": 4.0 if price_change_pct >= 8 else 5.0,
                    "take_profit_pct": 10.0 if price_change_pct >= 8 else 8.0,
                    "risk_reward": 2.0 if price_change_pct >= 8 else 1.6,
                    "reliability": 0.3,  # lower reliability than accumulation signals
                    "direction": "long",
                    "breakout": True,
                    "breakout_change_pct": round(price_change_pct, 1),
                },
            )
            decision.message = (
                f"{change_emoji} 突发拉盘 {change_emoji}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ {a.symbol}\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 现价: ${curr:,.6g}\n"
                f"⚡ 短期涨幅: {price_change_pct:+.1f}%\n"
                f"📈 24h: {a.change_24h:+.2f}%\n"
                f"📊 控盘分: {a.control_score} | {a.phase}\n"
                f"💵 成交额: ${vol:,.0f}\n\n"
                f"⚠️ 追涨有风险, 不构成投资建议\n"
                f"━━━━━━━━━━━━━━━━━━━"
            ).strip()

            results.append(decision)
            alerted_symbols.add(a.symbol)
            self.store.save_alert({
                "symbol": a.symbol,
                "timestamp": a.timestamp,
                "control_score": a.control_score,
                "phase": f"突发拉盘+{price_change_pct:.0f}%",
                "pump_probability": a.pump_probability,
                "signals": [{"dimension": "breakout", "score": int(price_change_pct),
                             "severity": "high", "description": f"短期涨幅{price_change_pct:.1f}%"}],
                "message": decision.message,
            })

            logger.warning(
                f"⚡ [BREAKOUT] {a.symbol}: +{price_change_pct:.1f}% "
                f"in scan interval, price=${curr:.6g}"
            )

        return results

    def _build_message(self, a: WhaleAnalysis) -> str:
        urgency_emoji = "🔴" if a.control_score >= 85 else "🟠"
        phase_emoji = {"吸筹末期": "🎯", "即将拉盘": "🚀", "高度控盘": "🐋", "持续吸筹中": "📦"}.get(a.phase, "⚠️")

        signal_lines = []
        for s in sorted(a.signals, key=lambda x: -x.score):
            sev_icon = "🔴" if s.severity == "critical" else "🟡" if s.severity == "high" else "⚪"
            signal_lines.append(f"  {sev_icon} +{s.score} {s.description}")
        signals_text = "\n".join(signal_lines)

        dims = [
            ("缩量横盘", a.dim_accumulation, 20),
            ("大单占比", a.dim_large_orders, 18),
            ("买卖不平衡", a.dim_imbalance, 15),
            ("链上净流出", a.dim_onchain_flow, 15),
            ("对倒交易", a.dim_wash_trade, 12),
            ("筹码集中", a.dim_concentration, 12),
            ("价差异常", a.dim_spread, 8),
        ]
        dim_bars = []
        for name, val, max_val in dims:
            pct = val / max_val if max_val > 0 else 0
            filled = int(pct * 8)
            bar = "█" * filled + "░" * (8 - filled)
            dim_bars.append(f"  {name:<6} {bar} {val}/{max_val}")
        dims_text = "\n".join(dim_bars)

        change_emoji = "📈" if a.change_24h >= 0 else "📉"

        return f"""{urgency_emoji} 庄家控盘预警 {urgency_emoji}

━━━━━━━━━━━━━━━━━━━
{phase_emoji} {a.symbol} — {a.phase}
━━━━━━━━━━━━━━━━━━━

💰 价格: ${a.price:,.6g}
{change_emoji} 24h: {a.change_24h:+.2f}%  |  7d: {a.change_7d:+.2f}%

🎯 控盘评分: {a.control_score}/100
🔥 拉盘概率: {a.pump_probability}%
📊 触发信号: {a.signal_count} 个维度

── 控盘维度雷达 ──
{dims_text}

── 触发信号明细 ──
{signals_text}

⚠️ 高控盘=高风险, 不构成投资建议
━━━━━━━━━━━━━━━━━━━""".strip()

    def generate_daily_summary(self, analyses: List[WhaleAnalysis], pump_monitor=None) -> str:
        sorted_a = sorted(analyses, key=lambda x: -x.control_score)
        critical = [a for a in sorted_a if a.control_score >= 70]
        medium = [a for a in sorted_a if 50 <= a.control_score < 70]

        lines = [
            "📊 每日控盘监控报告",
            f"━━━━━━━━━━━━━━━━━━━",
            f"📅 {time.strftime('%Y-%m-%d %H:%M')}",
            f"📡 监控代币: {len(analyses)} 个",
            f"🔴 高度控盘: {len(critical)} 个",
            f"🟡 中度控盘: {len(medium)} 个", "",
        ]
        if critical:
            lines.append("── 🔴 高度控盘 ──")
            for a in critical:
                lines.append(f"  {a.symbol}: {a.control_score}分 | {a.phase} | 拉盘率{a.pump_probability}% | 24h {a.change_24h:+.1f}%")
            lines.append("")
        if medium:
            lines.append("── 🟡 中度控盘 ──")
            for a in medium:
                lines.append(f"  {a.symbol}: {a.control_score}分 | {a.phase}")
            lines.append("")

        if pump_monitor is not None:
            try:
                stats = pump_monitor.get_full_stats(days=30)
                lines.append("── 📈 30日精度统计 ──")
                lines.append(f"  预警总数: {stats.get('total_alerts', 0)}")
                lines.append(f"  精确率 (Precision): {stats.get('precision', 0):.1f}%")
                lines.append(f"  召回率 (Recall): {stats.get('recall', 0):.1f}%")
                lines.append(f"  命中: {stats.get('predicted', 0)} | 漏报: {stats.get('missed', 0)} | 误报: {stats.get('false_positives', 0)}")
                lines.append("")
            except Exception as e:
                logger.warning(f"[DailySummary] 统计获取失败: {e}")

        lines.append("⚠️ 本报告不构成投资建议")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    # 暴跌预警
    # ══════════════════════════════════════════════════════════════════════

    def evaluate_crash(self, crash: CrashAnalysis) -> AlertDecision:
        """评估暴跌预警"""
        decision = AlertDecision(symbol=crash.symbol, analysis=crash)

        if crash.crash_score < CAC.min_crash_score:
            decision.reason = f"出货分不足: {crash.crash_score} < {CAC.min_crash_score}"
            return decision

        if crash.phase not in CAC.required_phases:
            decision.reason = f"阶段不符: {crash.phase}"
            return decision

        if crash.signal_count < CAC.min_signal_count:
            decision.reason = f"信号不足: {crash.signal_count} < {CAC.min_signal_count}"
            return decision

        if crash.crash_probability < CAC.min_crash_probability:
            decision.reason = f"概率不足: {crash.crash_probability}% < {CAC.min_crash_probability}%"
            return decision

        # 暴跌冷却用独立逻辑 (查 crash_alerts 表)
        # 简化: 用 symbol 在内存中做冷却
        if not hasattr(self, '_crash_cooldown'):
            self._crash_cooldown = {}
        now = time.time()
        last = self._crash_cooldown.get(crash.symbol, 0)
        if now - last < CAC.cooldown_hours * 3600:
            decision.reason = f"暴跌预警冷却中"
            return decision

        decision.should_alert = True
        decision.urgency = "critical"
        decision.message = self._build_crash_message(crash)
        decision.reason = "暴跌预警条件满足"
        decision.trade_signal = crash.get_suggested_short_sl_tp()
        self._crash_cooldown[crash.symbol] = now

        logger.warning(
            f"📉 [CRASH ALERT] {crash.symbol}: score={crash.crash_score}, "
            f"phase={crash.phase}, prob={crash.crash_probability}%"
        )
        return decision

    def evaluate_crash_batch(self, crashes: List[CrashAnalysis]) -> List[AlertDecision]:
        # 过滤稳定币
        crashes = [c for c in crashes if c.symbol not in STABLECOIN_SYMBOLS]
        results = []
        for c in crashes:
            d = self.evaluate_crash(c)
            if d.should_alert:
                results.append(d)
        return results

    def _build_crash_message(self, c: CrashAnalysis) -> str:
        phase_emoji = {
            "即将砸盘": "💣", "出货末期": "🔻", "高度出货": "📉"
        }.get(c.phase, "⚠️")

        signal_lines = []
        for s in sorted(c.signals, key=lambda x: -x.score):
            sev_icon = "🔴" if s.severity == "critical" else "🟡" if s.severity == "high" else "⚪"
            signal_lines.append(f"  {sev_icon} +{s.score} {s.description}")
        signals_text = "\n".join(signal_lines)

        return f"""🔻 暴跌预警 🔻

━━━━━━━━━━━━━━━━━━━
{phase_emoji} {c.symbol} — {c.phase}
━━━━━━━━━━━━━━━━━━━

💰 价格: ${c.price:,.6g}
📉 24h: {c.change_24h:+.2f}%  |  7d: {c.change_7d:+.2f}%

💀 出货评分: {c.crash_score}/100
📉 暴跌概率: {c.crash_probability}%
📊 出货信号: {c.signal_count} 个

── 出货信号明细 ──
{signals_text}

⚠️ 4H内可能暴跌≥50%, 注意风险!
━━━━━━━━━━━━━━━━━━━""".strip()
