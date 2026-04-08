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
import logging
from typing import List, Optional
from dataclasses import dataclass

from config import ALERT_CONFIG as AC, CRASH_ALERT_CONFIG as CAC
from analysis.whale_detector import WhaleAnalysis, CrashAnalysis
from data.data_store import DataStore

logger = logging.getLogger(__name__)


@dataclass
class AlertDecision:
    should_alert: bool = False
    symbol: str = ""
    reason: str = ""
    urgency: str = "normal"
    message: str = ""
    analysis: Optional[WhaleAnalysis] = None


class AlertEngine:
    def __init__(self, store: DataStore):
        self.store = store

    def evaluate(self, analysis: WhaleAnalysis) -> AlertDecision:
        decision = AlertDecision(symbol=analysis.symbol, analysis=analysis)

        if analysis.control_score < AC.min_control_score:
            decision.reason = f"控盘分不足: {analysis.control_score} < {AC.min_control_score}"
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

        last_alert_ts = self.store.get_last_alert_time(analysis.symbol)
        if last_alert_ts:
            hours_since = (time.time() * 1000 - last_alert_ts) / 3600000
            if hours_since < AC.cooldown_hours:
                decision.reason = f"冷却中: {hours_since:.1f}h < {AC.cooldown_hours}h"
                return decision

        decision.should_alert = True
        decision.urgency = "critical" if analysis.control_score >= 85 else "high"
        decision.message = self._build_message(analysis)
        decision.reason = "全部条件满足"

        logger.warning(f"🚨 [ALERT] {analysis.symbol}: score={analysis.control_score}, phase={analysis.phase}, pump={analysis.pump_probability}%")
        return decision

    def evaluate_batch(self, analyses: List[WhaleAnalysis]) -> List[AlertDecision]:
        results = []
        for a in analyses:
            decision = self.evaluate(a)
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
                logger.debug(f"[Alert] {a.symbol}: 不触发 - {decision.reason}")
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

    def generate_daily_summary(self, analyses: List[WhaleAnalysis]) -> str:
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
        lines.append("⚠️ 本报告不构成投资建议")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    # 暴跌预警
    # ══════════════════════════════════════════════════════════════════════

    def evaluate_crash(self, crash: CrashAnalysis) -> AlertDecision:
        """评估暴跌预警"""
        decision = AlertDecision(symbol=crash.symbol)

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
        self._crash_cooldown[crash.symbol] = now

        logger.warning(
            f"📉 [CRASH ALERT] {crash.symbol}: score={crash.crash_score}, "
            f"phase={crash.phase}, prob={crash.crash_probability}%"
        )
        return decision

    def evaluate_crash_batch(self, crashes: List[CrashAnalysis]) -> List[AlertDecision]:
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
