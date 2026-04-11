"""
可学习模型参数 (Model Parameters)

这些参数控制检测模型的行为, 学习引擎可以在运行时修改它们。
与 config.py 中的 THRESHOLDS (检测阈值) 不同, 这里定义的是:
  - 各维度的评分权重上限 (控制各维度对总分的贡献)
  - 各维度内部 critical/high/medium 的具体分值
  - 拉盘概率估算公式的系数
  - 阶段判定的门槛

相当于神经网络中的"权重", 而 THRESHOLDS 相当于"激活函数的参数"。
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass
class DimensionWeight:
    """单个维度的评分参数"""
    max_score: int          # 该维度最高分
    critical_score: int     # critical 级别得分
    high_score: int         # high 级别得分
    medium_score: int       # medium 级别得分


@dataclass
class ModelParams:
    """
    完整的模型参数集 — 校准目标: 预测 24h ≥30% 暴涨

    30% 暴涨比50%更常见, 模型可以适当宽松:
    - 基础概率系数提高 (30%事件概率更大)
    - 已涨惩罚减小 (涨了20%还可能再涨30%)
    - 阶段门槛略降
    """

    # ═══ 维度权重 ═══
    w_accumulation: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=20, critical_score=20, high_score=12, medium_score=5))
    w_large_orders: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=18, critical_score=18, high_score=10, medium_score=4))
    w_imbalance: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=15, critical_score=15, high_score=8, medium_score=3))
    w_onchain_flow: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=15, critical_score=15, high_score=9, medium_score=4))
    w_wash_trade: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=12, critical_score=12, high_score=6, medium_score=0))
    w_concentration: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=12, critical_score=12, high_score=6, medium_score=2))
    w_spread: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=8, critical_score=8, high_score=4, medium_score=0))
    w_funding: DimensionWeight = field(
        default_factory=lambda: DimensionWeight(max_score=8, critical_score=8, high_score=4, medium_score=2))

    # ═══ 拉盘概率模型系数 ═══
    prob_base_coeff: float = 0.55       # 提高 (30%事件比50%常见)
    prob_phase_bonus: float = 15.0
    prob_bb_squeeze_bonus: float = 8.0
    prob_macd_bonus: float = 5.0
    prob_ma_align_bonus: float = 5.0
    prob_overbought_penalty: float = 15.0
    prob_already_pumped_penalty: float = 12.0  # 降低 (7日涨30%仍可能再涨30%)

    # ═══ 阶段判定门槛 ═══
    phase_high_score: int = 75
    phase_mid_score: int = 50
    phase_low_score: int = 30
    phase_accum_min: int = 12
    phase_outflow_min: int = 9
    phase_imbalance_min: int = 8
    phase_large_orders_min: int = 10
    phase_bb_squeeze_width: float = 5.0

    # ═══ 市场筛选参数 ═══
    min_volume_24h: int = 500000              # 最低 24h 成交额 (USDT) - P1-2: 提高到 500K
    large_cap_score_penalty: int = 20         # 大盘股扣分 - P1-3: 消除 LTC/DOGE 等误报

    def get_weight(self, dimension: str) -> DimensionWeight:
        """按维度名获取权重"""
        mapping = {
            "accumulation": self.w_accumulation,
            "缩量横盘": self.w_accumulation,
            "large_orders": self.w_large_orders,
            "大单占比": self.w_large_orders,
            "imbalance": self.w_imbalance,
            "买卖不平衡": self.w_imbalance,
            "onchain_flow": self.w_onchain_flow,
            "链上净流出": self.w_onchain_flow,
            "wash_trade": self.w_wash_trade,
            "对倒交易": self.w_wash_trade,
            "concentration": self.w_concentration,
            "筹码集中": self.w_concentration,
            "spread": self.w_spread,
            "价差异常": self.w_spread,
            "funding": self.w_funding,
            "资金费率": self.w_funding,
        }
        return mapping.get(dimension)

    def get_weight_field_name(self, dimension: str) -> str:
        """获取维度对应的字段名"""
        mapping = {
            "accumulation": "w_accumulation", "缩量横盘": "w_accumulation",
            "large_orders": "w_large_orders", "大单占比": "w_large_orders",
            "imbalance": "w_imbalance", "买卖不平衡": "w_imbalance",
            "onchain_flow": "w_onchain_flow", "链上净流出": "w_onchain_flow",
            "wash_trade": "w_wash_trade", "对倒交易": "w_wash_trade",
            "concentration": "w_concentration", "筹码集中": "w_concentration",
            "spread": "w_spread", "价差异常": "w_spread",
            "funding": "w_funding", "资金费率": "w_funding",
        }
        return mapping.get(dimension, "")

    def to_dict(self) -> Dict:
        """序列化 (用于持久化)"""
        d = {}
        for dim in ["accumulation", "large_orders", "imbalance", "onchain_flow",
                     "wash_trade", "concentration", "spread", "funding"]:
            w = self.get_weight(dim)
            d[f"w_{dim}"] = asdict(w)
        d["prob_base_coeff"] = self.prob_base_coeff
        d["prob_phase_bonus"] = self.prob_phase_bonus
        d["prob_bb_squeeze_bonus"] = self.prob_bb_squeeze_bonus
        d["prob_macd_bonus"] = self.prob_macd_bonus
        d["prob_ma_align_bonus"] = self.prob_ma_align_bonus
        d["prob_overbought_penalty"] = self.prob_overbought_penalty
        d["prob_already_pumped_penalty"] = self.prob_already_pumped_penalty
        d["phase_high_score"] = self.phase_high_score
        d["phase_mid_score"] = self.phase_mid_score
        d["phase_low_score"] = self.phase_low_score
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "ModelParams":
        """从 dict 恢复"""
        params = cls()
        for dim in ["accumulation", "large_orders", "imbalance", "onchain_flow",
                     "wash_trade", "concentration", "spread", "funding"]:
            key = f"w_{dim}"
            if key in d and isinstance(d[key], dict):
                setattr(params, key, DimensionWeight(**d[key]))
        for k in ["prob_base_coeff", "prob_phase_bonus", "prob_bb_squeeze_bonus",
                   "prob_macd_bonus", "prob_ma_align_bonus",
                   "prob_overbought_penalty", "prob_already_pumped_penalty",
                   "phase_high_score", "phase_mid_score", "phase_low_score"]:
            if k in d:
                setattr(params, k, d[k])
        return params

    def describe_changes(self, other: "ModelParams") -> str:
        """对比两组参数的差异"""
        lines = []
        for dim in ["accumulation", "large_orders", "imbalance", "onchain_flow",
                     "wash_trade", "concentration", "spread", "funding"]:
            w_old = self.get_weight(dim)
            w_new = other.get_weight(dim)
            if w_old.max_score != w_new.max_score:
                lines.append(f"  {dim} 权重上限: {w_old.max_score} → {w_new.max_score}")
            if w_old.critical_score != w_new.critical_score:
                lines.append(f"  {dim} critical分: {w_old.critical_score} → {w_new.critical_score}")
            if w_old.high_score != w_new.high_score:
                lines.append(f"  {dim} high分: {w_old.high_score} → {w_new.high_score}")
        if self.prob_base_coeff != other.prob_base_coeff:
            lines.append(f"  概率基础系数: {self.prob_base_coeff} → {other.prob_base_coeff}")
        if self.prob_phase_bonus != other.prob_phase_bonus:
            lines.append(f"  阶段加分: {self.prob_phase_bonus} → {other.prob_phase_bonus}")
        if self.prob_overbought_penalty != other.prob_overbought_penalty:
            lines.append(f"  超买惩罚: {self.prob_overbought_penalty} → {other.prob_overbought_penalty}")
        return "\n".join(lines) if lines else "  无变化"


# 全局单例 — WhaleDetector 和 LearningEngine 共享
MODEL_PARAMS = ModelParams()
