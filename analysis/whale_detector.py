"""
庄家控盘特征检测引擎
7 维评分模型 + 阶段判定 + 拉盘概率估算
"""
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from config import THRESHOLDS as TH
from analysis.indicators import IndicatorResult
from analysis.model_params import MODEL_PARAMS
from data.onchain_client import OnchainMetrics

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """单个控盘信号"""
    dimension: str       # 维度名称
    severity: str        # critical / high / medium / low
    score: int           # 该信号贡献的分数
    description: str     # 人类可读描述
    raw_value: float = 0 # 原始数值

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension,
            "severity": self.severity,
            "score": self.score,
            "description": self.description,
            "raw_value": self.raw_value,
        }


@dataclass
class WhaleAnalysis:
    """完整的控盘分析结果"""
    symbol: str
    timestamp: int = 0

    # 综合评分
    control_score: int = 0          # 0-100
    signals: List[Signal] = field(default_factory=list)
    signal_count: int = 0           # 触发的信号数

    # 阶段判定
    phase: str = "观望"
    phase_color: str = "#64748b"

    # 拉盘概率
    pump_probability: int = 0       # 0-100

    # 各维度详细值
    dim_accumulation: int = 0       # 缩量横盘得分
    dim_large_orders: int = 0       # 大单占比得分
    dim_imbalance: int = 0          # 买卖不平衡得分
    dim_onchain_flow: int = 0       # 链上净流出得分
    dim_wash_trade: int = 0         # 对倒交易得分
    dim_concentration: int = 0      # 筹码集中得分
    dim_spread: int = 0             # 价差异常得分
    dim_funding_rate: int = 0       # 资金费率得分
    dim_momentum: int = 0           # 动量构建得分

    signal_reliability: float = 0.0      # 0-1 signal reliability score

    # 原始数据
    price: float = 0
    change_24h: float = 0
    change_7d: float = 0
    volume_24h: float = 0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "control_score": self.control_score,
            "signals": [s.to_dict() for s in self.signals],
            "signal_count": self.signal_count,
            "phase": self.phase,
            "phase_color": self.phase_color,
            "pump_probability": self.pump_probability,
            "signal_reliability": self.signal_reliability,
            "dimensions": {
                "accumulation": self.dim_accumulation,
                "large_orders": self.dim_large_orders,
                "imbalance": self.dim_imbalance,
                "onchain_flow": self.dim_onchain_flow,
                "wash_trade": self.dim_wash_trade,
                "concentration": self.dim_concentration,
                "spread": self.dim_spread,
                "funding_rate": self.dim_funding_rate,
                "momentum": self.dim_momentum,
            },
            "price": self.price,
            "change_24h": self.change_24h,
            "change_7d": self.change_7d,
            "volume_24h": self.volume_24h,
        }

    def get_suggested_sl_tp(self, indicators=None) -> dict:
        """Suggest stop-loss and take-profit based on volatility and phase"""
        if indicators and hasattr(indicators, 'atr_pct') and indicators.atr_pct > 0:
            sl_pct = max(5.0, min(15.0, indicators.atr_pct * 1.5))
        else:
            sl_pct = 8.0
        phase_tp = {"吸筹末期": 20.0, "即将拉盘": 18.0, "高度控盘": 15.0, "持续吸筹中": 12.0, "疑似吸筹": 10.0}
        tp_pct = phase_tp.get(self.phase, 12.0)
        if tp_pct / sl_pct < 1.5:
            tp_pct = sl_pct * 1.5
        return {"stop_loss_pct": round(sl_pct, 1), "take_profit_pct": round(tp_pct, 1),
                "risk_reward": round(tp_pct / sl_pct, 2), "reliability": round(self.signal_reliability, 2), "direction": "long"}


class WhaleDetector:
    """
    庄家控盘特征检测器

    7 维评分模型:
    1. 缩量横盘 (吸筹) — 最高 20 分
    2. 大单占比 — 最高 18 分
    3. 买卖盘不平衡 — 最高 15 分
    4. 链上净流出 — 最高 15 分
    5. 对倒交易 — 最高 12 分
    6. 筹码集中度 — 最高 12 分
    7. 价差异常 — 最高 8 分
    总计: 最高 100 分
    """

    def analyze(
        self,
        symbol: str,
        indicators: IndicatorResult,
        onchain: Optional[OnchainMetrics] = None,
        orderbook_spread: float = 0,
        orderbook_ratio: float = 0.5,
        trade_stats_large_pct: float = 0,
        trade_stats_buy_ratio: float = 0.5,
        timestamp: int = 0,
        funding_rate: Optional[float] = None,
    ) -> WhaleAnalysis:
        """执行完整的控盘分析"""
        result = WhaleAnalysis(symbol=symbol, timestamp=timestamp)
        result.price = indicators.price
        result.change_24h = indicators.change_24h
        result.change_7d = indicators.change_7d
        result.volume_24h = indicators.vol_current

        # ── 维度 1: 缩量横盘 (吸筹特征) ─────────────────────────────────
        self._score_accumulation(result, indicators)

        # ── 维度 2: 大单占比 ─────────────────────────────────────────────
        self._score_large_orders(result, indicators, trade_stats_large_pct)

        # ── 维度 3: 买卖盘不平衡 ─────────────────────────────────────────
        self._score_imbalance(result, indicators, orderbook_ratio, trade_stats_buy_ratio)

        # ── 维度 4: 链上净流出 ───────────────────────────────────────────
        self._score_onchain_flow(result, onchain)

        # ── 维度 5: 对倒交易估算 ─────────────────────────────────────────
        self._score_wash_trade(result, indicators)

        # ── 维度 6: 筹码集中度 ───────────────────────────────────────────
        self._score_concentration(result, onchain)

        # ── 维度 7: 价差异常 ─────────────────────────────────────────────
        self._score_spread(result, orderbook_spread)

        # ── 维度 8: 资金费率 ─────────────────────────────────────────────
        self._score_funding_rate(result, indicators, funding_rate)

        # ── 维度 9: 动量构建 ─────────────────────────────────────────────
        self._score_momentum_building(result, indicators)

        # ── 汇总 ─────────────────────────────────────────────────────────
        result.control_score = min(100, sum(s.score for s in result.signals))
        result.signal_count = len(result.signals)

        # ── 组合加分 (Combination Bonus) ──
        # 研究历史拉盘数据发现: 86%的拉盘前有"动量构建+价差异常+买卖不平衡"同时触发
        # 即使各维度分数不高, 多维度同时出现本身就是强信号
        active_dims = {s.dimension for s in result.signals}
        combo_bonus = 0

        # Multi-dimension bonus: 3+ dimensions firing simultaneously
        if result.signal_count >= 4:
            combo_bonus += 8  # 4维度同时触发 +8
        elif result.signal_count >= 3:
            combo_bonus += 5  # 3维度同时触发 +5

        # Golden combo: 动量构建 + 价差异常 + 买卖不平衡 (86% of historical pumps)
        golden_combo = {"动量构建", "价差异常", "买卖不平衡"}
        if golden_combo.issubset(active_dims):
            combo_bonus += 10  # 黄金组合 +10

        # Secondary combo: 动量构建 + 价差异常 + 缩量横盘
        accum_combo = {"动量构建", "价差异常", "缩量横盘"}
        if accum_combo.issubset(active_dims):
            combo_bonus += 7

        if combo_bonus > 0:
            result.control_score = min(100, result.control_score + combo_bonus)
            logger.debug(
                f"[Whale] {symbol}: combo_bonus=+{combo_bonus} "
                f"(dims={result.signal_count}, active={active_dims})"
            )

        # ── Signal Reliability ──
        unique_dims = len({s.dimension for s in result.signals})
        result.signal_reliability = min(1.0, unique_dims / 8.0)
        critical_count = sum(1 for s in result.signals if s.severity == "critical")
        if critical_count >= 2:
            result.signal_reliability = min(1.0, result.signal_reliability + 0.2)
        if result.dim_onchain_flow > 0 and result.dim_imbalance > 0:
            result.signal_reliability = min(1.0, result.signal_reliability + 0.15)
        if not any(s.severity in ("critical", "high") for s in result.signals):
            result.signal_reliability = max(0.0, result.signal_reliability - 0.2)

        # ── 阶段判定 ─────────────────────────────────────────────────────
        self._determine_phase(result, indicators, onchain)

        # ── 拉盘概率 ─────────────────────────────────────────────────────
        self._estimate_pump_probability(result, indicators, funding_rate)

        logger.info(
            f"[Whale] {symbol}: score={result.control_score}, "
            f"phase={result.phase}, pump={result.pump_probability}%, "
            f"signals={result.signal_count}"
        )
        return result

    # ── 各维度评分实现 ───────────────────────────────────────────────────

    def _score_accumulation(self, result: WhaleAnalysis, ind: IndicatorResult):
        """缩量横盘评分"""
        w = MODEL_PARAMS.w_accumulation
        ratio = ind.vol_shrink_ratio
        price_range = ind.price_range_30d

        if ratio < TH.vol_shrink_critical and price_range < TH.price_range_narrow:
            score = w.critical_score
            sev = "critical"
            desc = f"强烈缩量横盘: 量降至{ratio:.0%}, 振幅{price_range:.1%}"
        elif ratio < TH.vol_shrink_high and price_range < TH.price_range_medium:
            score = w.high_score
            sev = "high"
            desc = f"缩量横盘: 量降至{ratio:.0%}, 振幅{price_range:.1%}"
        elif ratio < 0.8 and price_range < 0.25:
            score = w.medium_score
            sev = "medium"
            desc = f"轻度缩量: 量降至{ratio:.0%}"
        else:
            return

        result.dim_accumulation = score
        result.signals.append(Signal("缩量横盘", sev, score, desc, ratio))

    def _score_large_orders(self, result: WhaleAnalysis, ind: IndicatorResult, trade_large_pct: float):
        """大单占比评分"""
        w = MODEL_PARAMS.w_large_orders
        pct = trade_large_pct
        size_ratio = ind.avg_trade_size_7d / ind.avg_trade_size_prev if ind.avg_trade_size_prev > 0 else 1

        if pct > TH.large_order_critical or size_ratio > 3:
            score = w.critical_score
            sev = "critical"
            desc = f"大单占比极高: {pct:.1f}%, 均笔放大{size_ratio:.1f}x"
        elif pct > TH.large_order_high or size_ratio > 2:
            score = w.high_score
            sev = "high"
            desc = f"大单占比偏高: {pct:.1f}%, 均笔{size_ratio:.1f}x"
        elif pct > 15 or size_ratio > 1.5:
            score = w.medium_score
            sev = "medium"
            desc = f"大单占比: {pct:.1f}%"
        else:
            return

        result.dim_large_orders = score
        result.signals.append(Signal("大单占比", sev, score, desc, pct))

    def _score_imbalance(self, result: WhaleAnalysis, ind: IndicatorResult, ob_ratio: float, buy_ratio: float):
        """买卖盘不平衡评分"""
        w = MODEL_PARAMS.w_imbalance
        avg_ratio = (ob_ratio + ind.taker_buy_ratio_7d + buy_ratio) / 3

        if avg_ratio > TH.imbalance_critical:
            score = w.critical_score
            sev = "critical"
            desc = f"买盘强势: 综合买入比{avg_ratio:.0%} (深度{ob_ratio:.0%}/K线{ind.taker_buy_ratio_7d:.0%}/成交{buy_ratio:.0%})"
        elif avg_ratio > TH.imbalance_high:
            score = w.high_score
            sev = "high"
            desc = f"买盘偏强: 综合买入比{avg_ratio:.0%}"
        elif avg_ratio > 0.52:
            score = w.medium_score
            sev = "medium"
            desc = f"买盘略强: {avg_ratio:.0%}"
        else:
            return

        result.dim_imbalance = score
        result.signals.append(Signal("买卖不平衡", sev, score, desc, avg_ratio))

    def _score_onchain_flow(self, result: WhaleAnalysis, onchain: Optional[OnchainMetrics]):
        """链上净流出评分"""
        if not onchain:
            return
        w = MODEL_PARAMS.w_onchain_flow
        net_count = onchain.exchange_outflow_count - onchain.exchange_inflow_count

        if net_count > TH.net_outflow_critical:
            score = w.critical_score
            sev = "critical"
            desc = f"大量从交易所转出: 净流出{net_count}笔, 金额${onchain.net_flow:,.0f}"
        elif net_count > TH.net_outflow_high:
            score = w.high_score
            sev = "high"
            desc = f"链上净流出: {net_count}笔"
        elif net_count > 30:
            score = w.medium_score
            sev = "medium"
            desc = f"轻度净流出: {net_count}笔"
        else:
            return

        result.dim_onchain_flow = score
        result.signals.append(Signal("链上净流出", sev, score, desc, net_count))

    def _score_wash_trade(self, result: WhaleAnalysis, ind: IndicatorResult):
        """对倒交易估算评分"""
        if ind.vol_spike_ratio < 1.5:
            return
        w = MODEL_PARAMS.w_wash_trade
        vol_price_ratio = ind.vol_spike_ratio / (abs(ind.change_24h) + 0.5) * 10
        wash_est = min(60, vol_price_ratio * 5)

        if wash_est > TH.wash_trade_critical:
            score = w.critical_score
            sev = "critical"
            desc = f"疑似对倒: 量增{ind.vol_spike_ratio:.1f}x但价格仅动{abs(ind.change_24h):.1f}%"
        elif wash_est > TH.wash_trade_high:
            score = w.high_score
            sev = "high"
            desc = f"轻度对倒嫌疑: 量价背离"
        else:
            return

        result.dim_wash_trade = score
        result.signals.append(Signal("对倒交易", sev, score, desc, wash_est))

    def _score_concentration(self, result: WhaleAnalysis, onchain: Optional[OnchainMetrics]):
        """筹码集中度评分"""
        if not onchain:
            return
        w = MODEL_PARAMS.w_concentration
        top10 = onchain.top10_holders_pct
        holder_change = onchain.holder_count_change_7d

        score = 0
        parts = []
        if top10 > TH.top10_hold_critical:
            score += int(w.critical_score * 0.67)
            parts.append(f"Top10持仓{top10:.0f}%")
        if holder_change < -TH.holder_decrease_critical:
            score += int(w.critical_score * 0.33)
            parts.append(f"地址减少{abs(holder_change)}")
        elif holder_change < -TH.holder_decrease_high:
            score += int(w.high_score * 0.33)
            parts.append(f"地址减少{abs(holder_change)}")

        if score == 0:
            return
        score = min(w.max_score, score)
        sev = "critical" if score >= w.critical_score * 0.8 else "high" if score >= w.high_score * 0.8 else "medium"
        desc = "筹码集中: " + ", ".join(parts)
        result.dim_concentration = score
        result.signals.append(Signal("筹码集中", sev, score, desc, top10))

    def _score_spread(self, result: WhaleAnalysis, spread_pct: float):
        """价差异常评分"""
        if spread_pct <= 0:
            return
        w = MODEL_PARAMS.w_spread
        if spread_pct < TH.spread_narrow_critical:
            score = w.critical_score
            sev = "high"
            desc = f"价差异常窄: {spread_pct:.3f}% (疑似做市商控制)"
        elif spread_pct < 0.15:
            score = w.high_score
            sev = "medium"
            desc = f"价差偏窄: {spread_pct:.3f}%"
        else:
            return
        result.dim_spread = score
        result.signals.append(Signal("价差异常", sev, score, desc, spread_pct))

    def _score_funding_rate(self, result: WhaleAnalysis, ind: IndicatorResult, funding_rate: Optional[float]):
        """资金费率评分 — 负费率+吸筹=拉盘前兆"""
        if funding_rate is None:
            return
        w = MODEL_PARAMS.w_funding
        has_accumulation = result.dim_accumulation >= 5

        if funding_rate < TH.funding_rate_critical and has_accumulation:
            score = w.critical_score
            sev = "critical"
            desc = f"极端空头费率{funding_rate*100:.3f}%+吸筹信号=拉盘前兆"
        elif funding_rate < TH.funding_rate_bullish and has_accumulation:
            score = w.high_score
            sev = "high"
            desc = f"空头付费{funding_rate*100:.3f}%+吸筹=看涨"
        elif funding_rate < TH.funding_rate_bullish:
            score = w.medium_score
            sev = "medium"
            desc = f"轻度空头拥挤: 费率{funding_rate*100:.3f}%"
        else:
            return

        result.dim_funding_rate = score
        result.signals.append(Signal("资金费率", sev, score, desc, funding_rate))

    def _score_momentum_building(self, result: WhaleAnalysis, ind: IndicatorResult):
        """动量构建评分 - 检测拉盘前的动量积蓄"""
        score = 0
        parts = []

        # RSI between 50-70 (building momentum, not overbought) - max 10 points
        if 50 <= ind.rsi_14 <= 70:
            rsi_score = 10
            parts.append(f"RSI{ind.rsi_14:.0f}动量构建中")
            score += rsi_score
        elif 40 <= ind.rsi_14 < 50:
            rsi_score = 5
            parts.append(f"RSI{ind.rsi_14:.0f}起步")
            score += rsi_score

        # BB Width < 5% (tight consolidation before breakout) - max 5 points
        if 0 < ind.bb_width < 5.0:
            bb_score = 5
            parts.append(f"BB窄{ind.bb_width:.2f}%")
            score += bb_score

        if score == 0:
            return

        # Cap at 15 points
        score = min(15, score)

        # Determine severity
        sev = "critical" if score >= 12 else "high" if score >= 8 else "medium"
        desc = "动量构建: " + ", ".join(parts)

        result.dim_momentum = score
        result.signals.append(Signal("动量构建", sev, score, desc, score))

    # ── 阶段判定 ─────────────────────────────────────────────────────────

    def _determine_phase(self, result: WhaleAnalysis, ind: IndicatorResult, onchain: Optional[OnchainMetrics]):
        """根据综合信号判定当前阶段"""
        mp = MODEL_PARAMS
        score = result.control_score
        has_accumulation = result.dim_accumulation >= mp.phase_accum_min
        has_outflow = result.dim_onchain_flow >= mp.phase_outflow_min
        has_imbalance = result.dim_imbalance >= mp.phase_imbalance_min
        has_large_orders = result.dim_large_orders >= mp.phase_large_orders_min
        bb_squeeze = ind.bb_width < mp.phase_bb_squeeze_width and ind.bb_width > 0

        if score >= mp.phase_high_score:
            if has_accumulation and has_imbalance and (has_outflow or bb_squeeze):
                result.phase = "吸筹末期"
                result.phase_color = "#ff2d55"
            elif has_accumulation and has_large_orders:
                result.phase = "即将拉盘"
                result.phase_color = "#ff4757"
            elif has_outflow:
                result.phase = "持续吸筹中"
                result.phase_color = "#ff9f43"
            else:
                result.phase = "高度控盘"
                result.phase_color = "#f97316"
        elif score >= mp.phase_mid_score:
            if has_accumulation:
                result.phase = "疑似吸筹"
                result.phase_color = "#eab308"
            else:
                result.phase = "中度控盘"
                result.phase_color = "#eab308"
        elif score >= mp.phase_low_score:
            result.phase = "轻度异常"
            result.phase_color = "#3b82f6"
        else:
            result.phase = "市场化交易"
            result.phase_color = "#64748b"

    def _estimate_pump_probability(self, result: WhaleAnalysis, ind: IndicatorResult, funding_rate: Optional[float] = None):
        """基于控盘评分和辅助信号估算短期拉盘概率"""
        mp = MODEL_PARAMS
        base = result.control_score * mp.prob_base_coeff

        bonuses = 0
        if result.phase in ("吸筹末期", "即将拉盘"):
            bonuses += mp.prob_phase_bonus
        elif result.phase == "疑似吸筹":
            bonuses += 8
        elif result.phase in ("中度控盘", "高度控盘"):
            bonuses += 5
        if ind.bb_width < mp.phase_bb_squeeze_width and ind.bb_width > 0:
            bonuses += mp.prob_bb_squeeze_bonus
        if ind.macd_histogram > 0 and ind.rsi_14 < 60:
            bonuses += mp.prob_macd_bonus
        if ind.sma7 > ind.sma20 > ind.sma60:
            bonuses += mp.prob_ma_align_bonus
        if funding_rate is not None and funding_rate < -0.0003:
            bonuses += 8  # 空头付费 = 拉盘燃料

        penalties = 0
        if ind.rsi_14 > 75:
            penalties += mp.prob_overbought_penalty
        if ind.change_7d > 30:
            penalties += mp.prob_already_pumped_penalty
        if funding_rate is not None and funding_rate > TH.funding_rate_bearish:
            penalties += 5  # 多头拥挤, 拉盘概率降低

        prob = base + bonuses - penalties
        # Weight by signal reliability
        if result.signal_reliability > 0:
            prob = prob * (0.5 + 0.5 * result.signal_reliability)
        result.pump_probability = max(0, min(95, int(prob)))


# ═══════════════════════════════════════════════════════════════════════════
# 暴跌检测器 (出货信号)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CrashAnalysis:
    """出货/暴跌分析结果"""
    symbol: str
    timestamp: int = 0
    crash_score: int = 0           # 0-100 出货评分
    signals: List[Signal] = field(default_factory=list)
    signal_count: int = 0
    phase: str = "正常"
    crash_probability: int = 0      # 0-100 暴跌概率

    # 各维度
    dim_vol_surge_drop: int = 0     # 放量下跌
    dim_sell_pressure: int = 0      # 卖盘压力
    dim_net_inflow: int = 0         # 链上净流入 (涌入交易所)
    dim_holder_surge: int = 0       # 散户暴增 (接盘侠)
    dim_rsi_divergence: int = 0     # RSI顶背离
    dim_ma_death_cross: int = 0     # 均线死叉
    dim_price_breakdown: int = 0    # 跌破关键支撑
    dim_funding_rate: int = 0       # 资金费率得分

    price: float = 0
    change_24h: float = 0
    change_7d: float = 0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol, "crash_score": self.crash_score,
            "phase": self.phase, "crash_probability": self.crash_probability,
            "signals": [s.to_dict() for s in self.signals],
        }

    def get_suggested_short_sl_tp(self, indicators=None) -> dict:
        """Suggest stop-loss and take-profit for short trades"""
        if indicators and hasattr(indicators, 'atr_pct') and indicators.atr_pct > 0:
            sl_pct = max(5.0, min(15.0, indicators.atr_pct * 1.5))
        else:
            sl_pct = 8.0
        phase_tp = {"即将砸盘": 20.0, "高度出货": 18.0, "出货末期": 15.0, "疑似出货": 10.0}
        tp_pct = phase_tp.get(self.phase, 12.0)
        if tp_pct / sl_pct < 1.5:
            tp_pct = sl_pct * 1.5
        return {"stop_loss_pct": round(sl_pct, 1), "take_profit_pct": round(tp_pct, 1),
                "risk_reward": round(tp_pct / sl_pct, 2), "reliability": 0.5, "direction": "short"}


class CrashDetector:
    """
    出货/暴跌检测器

    7 维出货评分 (与控盘检测反向):
    1. 放量下跌 — 最高 20 分 (价跌量增=恐慌出逃)
    2. 卖盘压力 — 最高 18 分 (卖单远超买单)
    3. 链上净流入 — 最高 18 分 (代币涌入交易所=准备砸盘)
    4. 散户暴增 — 最高 15 分 (地址数暴增=庄家出货散户接盘)
    5. RSI 顶背离 — 最高 12 分 (价格新高RSI不新高)
    6. 均线死叉 — 最高 10 分 (短均线下穿长均线)
    7. 跌破支撑 — 最高 7 分 (跌破布林带下轨/前低)
    """

    def analyze(
        self, symbol: str, indicators, onchain=None,
        orderbook_ratio: float = 0.5, trade_buy_ratio: float = 0.5,
        timestamp: int = 0, funding_rate: Optional[float] = None,
    ) -> CrashAnalysis:
        ind = indicators
        result = CrashAnalysis(symbol=symbol, timestamp=timestamp)
        result.price = ind.price
        result.change_24h = ind.change_24h
        result.change_7d = ind.change_7d

        # ── 1. 放量下跌 (最高20分) ──
        if ind.vol_spike_ratio > TH.dump_vol_surge_high and ind.change_24h < -5:
            if ind.vol_spike_ratio > TH.dump_vol_surge_critical and ind.change_24h < -15:
                s = 20; sev = "critical"
            elif ind.vol_spike_ratio > TH.dump_vol_surge_high:
                s = 12; sev = "high"
            else:
                s = 5; sev = "medium"
            result.dim_vol_surge_drop = s
            result.signals.append(Signal(
                "放量下跌", sev, s,
                f"量增{ind.vol_spike_ratio:.1f}x+跌{ind.change_24h:.1f}%",
                ind.vol_spike_ratio))

        # ── 2. 卖盘压力 (最高18分) ──
        sell_ratio = 1 - (orderbook_ratio + ind.taker_buy_ratio_7d + trade_buy_ratio) / 3
        if sell_ratio > TH.sell_pressure_critical - 0.28:  # >0.44 means buy<0.56
            if sell_ratio > 1 - (1 - TH.sell_pressure_critical):  # sell>72%
                s = 18; sev = "critical"
                desc = f"卖盘压倒性: 卖比{sell_ratio:.0%}"
            elif sell_ratio > 1 - (1 - TH.sell_pressure_high):
                s = 10; sev = "high"
                desc = f"卖盘偏强: {sell_ratio:.0%}"
            else:
                s = 4; sev = "medium"
                desc = f"卖盘略强"
            result.dim_sell_pressure = s
            result.signals.append(Signal("卖盘压力", sev, s, desc, sell_ratio))

        # ── 3. 链上净流入 (最高18分) ──
        if onchain:
            net_inflow = onchain.exchange_inflow_count - onchain.exchange_outflow_count
            if net_inflow > TH.net_inflow_critical:
                s = 18; sev = "critical"
                desc = f"大量流入交易所: 净流入{net_inflow}笔 (准备砸盘)"
            elif net_inflow > TH.net_inflow_high:
                s = 10; sev = "high"
                desc = f"链上净流入: {net_inflow}笔"
            else:
                net_inflow = 0; s = 0
            if s > 0:
                result.dim_net_inflow = s
                result.signals.append(Signal("链上净流入", sev, s, desc, net_inflow))

        # ── 4. 散户暴增 (最高15分) ──
        if onchain:
            hchg = onchain.holder_count_change_7d
            if hchg > TH.holder_increase_critical:
                s = 15; sev = "critical"
                desc = f"地址暴增{hchg}个 (散户蜂拥接盘)"
            elif hchg > TH.holder_increase_high:
                s = 8; sev = "high"
                desc = f"地址增加{hchg}个"
            else:
                s = 0
            if s > 0:
                result.dim_holder_surge = s
                result.signals.append(Signal("散户暴增", sev, s, desc, hchg))

        # ── 5. RSI顶背离 (最高12分) ──
        if ind.rsi_14 > 70 and ind.change_7d > 20:
            s = 12; sev = "high"
            desc = f"RSI={ind.rsi_14:.0f}超买 + 7日涨{ind.change_7d:.0f}%"
            result.dim_rsi_divergence = s
            result.signals.append(Signal("RSI超买", sev, s, desc, ind.rsi_14))
        elif ind.rsi_14 > 80:
            s = 8; sev = "medium"
            result.dim_rsi_divergence = s
            result.signals.append(Signal("RSI超买", sev, s, f"RSI={ind.rsi_14:.0f}极端超买", ind.rsi_14))

        # ── 6. 均线死叉 (最高10分) ──
        if ind.sma7 < ind.sma20 < ind.sma60 and ind.sma60 > 0:
            s = 10; sev = "high"
            desc = "均线空头排列 (7<20<60)"
            result.dim_ma_death_cross = s
            result.signals.append(Signal("均线死叉", sev, s, desc, 0))
        elif ind.sma7 < ind.sma20 and ind.sma20 > 0:
            s = 5; sev = "medium"
            result.dim_ma_death_cross = s
            result.signals.append(Signal("短均下穿", sev, s, "SMA7 < SMA20", 0))

        # ── 7. 跌破支撑 (最高7分) ──
        if ind.bb_lower > 0 and ind.price < ind.bb_lower:
            s = 7; sev = "high"
            desc = f"跌破布林带下轨 (${ind.price:.4f} < ${ind.bb_lower:.4f})"
            result.dim_price_breakdown = s
            result.signals.append(Signal("跌破支撑", sev, s, desc, ind.price))

        # ── 8. 资金费率 (最高8分) ──
        if funding_rate is not None:
            has_sell_pressure = result.dim_sell_pressure >= 4
            if funding_rate > TH.funding_rate_bearish and has_sell_pressure:
                s = 8; sev = "critical"
                desc = f"多头拥挤费率{funding_rate*100:.3f}%+抛压=暴跌前兆"
                result.dim_funding_rate = s
                result.signals.append(Signal("资金费率", sev, s, desc, funding_rate))
            elif funding_rate > 0.0005:
                s = 4; sev = "high"
                desc = f"多头偏拥挤: 费率{funding_rate*100:.3f}%"
                result.dim_funding_rate = s
                result.signals.append(Signal("资金费率", sev, s, desc, funding_rate))

        # ── 汇总 ──
        result.crash_score = min(100, sum(s.score for s in result.signals))
        result.signal_count = len(result.signals)

        # ── 阶段判定 ──
        if result.crash_score >= 70:
            if result.dim_net_inflow >= 10 and result.dim_vol_surge_drop >= 12:
                result.phase = "即将砸盘"
            elif result.dim_sell_pressure >= 10:
                result.phase = "高度出货"
            else:
                result.phase = "出货末期"
        elif result.crash_score >= 45:
            result.phase = "疑似出货"
        elif result.crash_score >= 25:
            result.phase = "轻度异常"

        # ── 暴跌概率 ──
        base = result.crash_score * 0.5
        if result.phase in ("即将砸盘", "出货末期"):
            base += 15
        if ind.rsi_14 > 75:
            base += 8
        if ind.change_7d > 40:
            base += 10  # 已暴涨→更可能暴跌
        if funding_rate is not None and funding_rate > TH.funding_rate_bearish:
            base += 8  # 多头拥挤加速暴跌
        if funding_rate is not None and funding_rate < -0.0005:
            base -= 5  # 空头拥挤→轧空风险, 降低暴跌概率
        result.crash_probability = max(0, min(95, int(base)))

        if result.crash_score > 0:
            logger.info(
                f"[Crash] {symbol}: score={result.crash_score}, "
                f"phase={result.phase}, prob={result.crash_probability}%"
            )
        return result

