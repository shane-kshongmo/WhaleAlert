"""
自学习引擎 (Learning Engine)

核心闭环:
  监控爆涨 → 发现漏报 → 回溯分析 → 提取规律 → 调整阈值 → 验证效果

学习策略:
1. 收集所有漏报的爆涨事件
2. 分析爆涨前的指标快照, 寻找被忽略的共性特征
3. 自动放宽相关维度的阈值 (小步调整, 避免过拟合)
4. 记录每次调整的原因和效果
5. 定期回测: 用新阈值重算历史数据, 看能否命中更多
"""
import json
import time
import math
import logging
import copy
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import Counter, defaultdict

from config import DetectionThresholds, THRESHOLDS, AlertConfig, ALERT_CONFIG
from data.data_store import DataStore
from analysis.pump_monitor import PumpMonitor, PumpEvent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 学习结果
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class LessonLearned:
    """从一次或一批漏报中学到的规律"""
    lesson_id: str                   # 唯一标识
    timestamp: int
    event_type: str                  # "missed_pump_analysis" / "threshold_adjust" / "pattern_discovery"
    summary: str                     # 人类可读总结
    missed_count: int = 0            # 涉及的漏报数
    symbols: List[str] = field(default_factory=list)

    # 发现的规律
    patterns: List[Dict] = field(default_factory=list)
    # e.g. [{"dimension": "缩量横盘", "finding": "漏报代币的 vol_shrink 平均 0.52, 当前阈值 0.6",
    #         "recommendation": "放宽到 0.65"}]

    # 阈值调整
    old_thresholds: Dict = field(default_factory=dict)
    new_thresholds: Dict = field(default_factory=dict)
    adjustments: List[Dict] = field(default_factory=list)
    # e.g. [{"param": "vol_shrink_high", "old": 0.6, "new": 0.65, "reason": "..."}]

    # 预估效果
    estimated_new_hit_rate: float = 0
    estimated_false_positive_increase: float = 0

    def to_dict(self) -> Dict:
        return {
            "lesson_id": self.lesson_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "summary": self.summary,
            "missed_count": self.missed_count,
            "symbols": self.symbols,
            "patterns": self.patterns,
            "adjustments": self.adjustments,
            "estimated_new_hit_rate": self.estimated_new_hit_rate,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 学习引擎
# ═══════════════════════════════════════════════════════════════════════════

class LearningEngine:
    """
    自学习引擎

    学习流程:
    1. analyze_missed_pumps()  — 分析所有漏报, 提取共性
    2. discover_patterns()     — 识别未被捕捉的控盘模式
    3. propose_adjustments()   — 生成阈值调整建议
    4. apply_adjustments()     — 应用调整 (带安全边界)
    5. evaluate_adjustment()   — 回测评估效果
    """

    # 安全边界: 单次调整最大幅度 (防止过拟合)
    MAX_ADJUST_PCT = 0.20       # 阈值单次最多调 20%
    MIN_SAMPLES = 3             # 至少 3 次漏报才触发学习
    LEARNING_COOLDOWN_H = 24    # 两次学习间隔至少 24 小时

    def __init__(self, store: DataStore, pump_monitor: PumpMonitor):
        self.store = store
        self.pump_monitor = pump_monitor
        self._current_thresholds = copy.deepcopy(THRESHOLDS)
        self._current_alert_config = copy.deepcopy(ALERT_CONFIG)
        self._last_learning_time = 0
        self._learning_history: List[LessonLearned] = []

    # ── 1. 主学习入口 ───────────────────────────────────────────────────

    def run_learning_cycle(self, days: int = 14) -> Optional[LessonLearned]:
        """
        执行一轮完整学习 (双向学习)

        学习来源 A: 漏报 (missed) → 放宽阈值, 提高召回率
        学习来源 B: 误报 (false positive) → 收紧阈值, 提高精确率
        两者权衡, 避免顾此失彼
        """
        now = time.time()

        # 冷却检查
        if now - self._last_learning_time < self.LEARNING_COOLDOWN_H * 3600:
            hours_left = (self.LEARNING_COOLDOWN_H - (now - self._last_learning_time) / 3600)
            logger.info(f"[Learning] 冷却中, 还需 {hours_left:.1f}h")
            return None

        # 获取漏报 + 误报
        missed = self.pump_monitor.get_missed_events(days=days)
        false_positives = self.pump_monitor.get_false_positives(days=days)
        total_evidence = len(missed) + len(false_positives)

        if total_evidence < self.MIN_SAMPLES:
            logger.info(f"[Learning] 样本不足: 漏报{len(missed)}+误报{len(false_positives)}={total_evidence} < {self.MIN_SAMPLES}")
            return None

        stats = self.pump_monitor.get_full_stats(days=days)
        logger.info(
            f"[Learning] 开始双向学习: {days}天内 "
            f"爆涨={stats['total_pumps']}, 命中={stats['predicted']}, "
            f"漏报={stats['missed']}, 误报={stats['false_positives']}, "
            f"精确率={stats['precision']:.1f}%, 召回率={stats['recall']:.1f}%"
        )

        all_adjustments = []
        all_patterns = []

        # ── A: 从漏报学习 (放宽阈值) ──
        if len(missed) >= 1:
            miss_patterns = self._analyze_missed_patterns(missed)
            miss_adjs = self._propose_adjustments(miss_patterns, missed)
            all_patterns.extend(miss_patterns)
            all_adjustments.extend(miss_adjs)

        # ── B: 从误报学习 (收紧阈值) ──
        if len(false_positives) >= 1:
            fp_patterns = self._analyze_false_positive_patterns(false_positives)
            fp_adjs = self._propose_fp_adjustments(fp_patterns, false_positives)
            all_patterns.extend(fp_patterns)
            all_adjustments.extend(fp_adjs)

        # ── 冲突仲裁: 如果同一参数既要放宽又要收紧 ──
        all_adjustments = self._resolve_conflicts(all_adjustments, stats)

        if not all_adjustments:
            logger.info("[Learning] 未发现可操作的调整建议")
            return None

        lesson = self._build_lesson(missed, all_patterns, all_adjustments, stats)
        lesson.event_type = "bidirectional_learning"
        lesson.summary = (
            f"双向学习: {len(missed)}漏报+{len(false_positives)}误报 → "
            + " | ".join(a["reason"][:40] for a in all_adjustments[:3])
        )
        self._apply_adjustments(lesson)
        self._save_lesson(lesson)

        self._last_learning_time = now
        self._learning_history.append(lesson)
        logger.warning(f"📚 [Learning] {lesson.summary}")
        return lesson

    # ── 2a. 分析误报的共性模式 ───────────────────────────────────────────

    def _analyze_false_positive_patterns(self, fps: List[Dict]) -> List[Dict]:
        """
        深度分析误报: 不只是"预测错了", 要理解哪些维度给了虚假信号

        核心问题:
        - 哪些维度在误报中贡献了高分但不应该? → 降低该维度权重
        - 概率模型的哪些加分项在误报中频繁触发? → 减弱系数
        - 误报代币有哪些共性特征? → 加入负面信号
        """
        patterns = []

        scores = [f.get("alert_score", 0) for f in fps]
        actual_changes = [f.get("actual_change_24h", 0) for f in fps]
        max_changes = [f.get("max_change_24h", 0) for f in fps]
        avg_actual = sum(actual_changes) / len(actual_changes) if actual_changes else 0
        avg_max = sum(max_changes) / len(max_changes) if max_changes else 0

        # ── 分析 1: 各维度在误报中的得分贡献 ──
        dim_total_scores = defaultdict(list)
        dim_trigger_count = Counter()

        for fp in fps:
            try:
                raw = fp.get("alert_signals_json", "[]")
                sigs = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(sigs, list):
                    for s in sigs:
                        if isinstance(s, dict):
                            dim = s.get("dimension", "")
                            dim_total_scores[dim].append(s.get("score", 0))
                            dim_trigger_count[dim] += 1
            except (json.JSONDecodeError, TypeError):
                pass

        # 找出"虚假贡献最大"的维度
        for dim, dim_scores in dim_total_scores.items():
            if not dim:
                continue
            avg_dim_score = sum(dim_scores) / len(dim_scores)
            trigger_rate = dim_trigger_count[dim] / len(fps)

            if trigger_rate >= 0.5 and avg_dim_score > 5:
                patterns.append({
                    "type": "fp_inflated_dimension",
                    "source": "false_positive",
                    "dimension": dim,
                    "finding": (
                        f"维度[{dim}]在{trigger_rate:.0%}误报中触发, "
                        f"平均贡献{avg_dim_score:.0f}分, 但代币实际{avg_actual:+.1f}% → 权重虚高"
                    ),
                    "avg_score": avg_dim_score,
                    "trigger_rate": trigger_rate,
                    "avg_actual_change": avg_actual,
                    "impact": "high",
                })

        # ── 分析 2: 概率模型过于乐观 ──
        avg_score = sum(scores) / len(scores) if scores else 0
        probs = [f.get("alert_pump_prob", 0) for f in fps]
        avg_prob = sum(probs) / len(probs) if probs else 0

        if avg_prob > 0 and avg_actual < 10:
            # 概率模型预测 X% 但实际只涨了 Y%
            overestimate = avg_prob - max(0, avg_max)
            if overestimate > 20:
                patterns.append({
                    "type": "fp_probability_overestimate",
                    "source": "false_positive",
                    "finding": (
                        f"概率模型过度乐观: 预测均{avg_prob:.0f}%, "
                        f"实际最高仅{avg_max:+.1f}%, 偏差{overestimate:.0f}%"
                    ),
                    "avg_prob": avg_prob,
                    "avg_max_change": avg_max,
                    "overestimate": overestimate,
                    "impact": "high",
                })

        # ── 分析 3: 误报后下跌 = 模型系统性偏差 ──
        fell_count = sum(1 for c in actual_changes if c < -5)
        if fell_count > len(fps) * 0.4:
            patterns.append({
                "type": "fp_systematic_bias",
                "source": "false_positive",
                "finding": (
                    f"{fell_count}/{len(fps)}次误报后下跌超5%, "
                    f"模型可能将'出货'误判为'吸筹'"
                ),
                "fell_rate": fell_count / len(fps),
                "impact": "critical",
            })

        # ── 分析 4: 阶段判定错误 ──
        phases = Counter()
        for fp in fps:
            phases[fp.get("alert_phase", "")] += 1
        for phase, cnt in phases.most_common(2):
            if cnt >= len(fps) * 0.3:
                patterns.append({
                    "type": "fp_phase_error",
                    "source": "false_positive",
                    "finding": f"阶段'{phase}'在{cnt}/{len(fps)}次误报中出现, 阶段判定逻辑需收紧",
                    "phase": phase,
                    "count": cnt,
                    "impact": "medium",
                })

        return patterns

    # ── 2b. 从误报调整模型参数 ────────────────────────────────────────────

    def _propose_fp_adjustments(self, patterns: List[Dict], fps: List[Dict]) -> List[Dict]:
        """
        从误报模式生成模型调整建议

        不只是改门槛, 而是:
        1. 降低虚假维度的权重 (max_score, critical_score, high_score)
        2. 调整概率模型系数 (减小 base_coeff, 增大 penalty)
        3. 收紧阶段判定门槛
        4. 收紧检测阈值 (让维度更难触发)
        """
        adjustments = []
        from analysis.model_params import MODEL_PARAMS

        for p in patterns:
            ptype = p.get("type", "")

            # ── 虚假维度 → 降低权重 + 收紧阈值 ──
            if ptype == "fp_inflated_dimension":
                dim = p.get("dimension", "")
                avg_score = p.get("avg_score", 0)
                trigger_rate = p.get("trigger_rate", 0)

                # 1) 降低维度权重上限
                field_name = MODEL_PARAMS.get_weight_field_name(dim)
                w = MODEL_PARAMS.get_weight(dim)
                if w and field_name:
                    # 权重降幅与误报中的贡献成比例, 但最多降 25%
                    reduction = min(0.25, trigger_rate * 0.3)
                    new_critical = max(3, int(w.critical_score * (1 - reduction)))
                    new_high = max(2, int(w.high_score * (1 - reduction)))
                    new_max = max(3, int(w.max_score * (1 - reduction)))

                    if new_critical < w.critical_score:
                        adjustments.append({
                            "param": f"{field_name}.critical_score",
                            "target": "model_params",
                            "direction": "tighten",
                            "dimension": dim,
                            "old": w.critical_score,
                            "new": new_critical,
                            "reason": f"[{dim}]误报贡献均{avg_score:.0f}分, critical降{w.critical_score}→{new_critical}",
                            "confidence": min(0.85, trigger_rate),
                        })
                    if new_high < w.high_score:
                        adjustments.append({
                            "param": f"{field_name}.high_score",
                            "target": "model_params",
                            "direction": "tighten",
                            "dimension": dim,
                            "old": w.high_score,
                            "new": new_high,
                            "reason": f"[{dim}]误报频触, high降{w.high_score}→{new_high}",
                            "confidence": min(0.8, trigger_rate),
                        })
                    if new_max < w.max_score:
                        adjustments.append({
                            "param": f"{field_name}.max_score",
                            "target": "model_params",
                            "direction": "tighten",
                            "dimension": dim,
                            "old": w.max_score,
                            "new": new_max,
                            "reason": f"[{dim}]权重上限降{w.max_score}→{new_max}",
                            "confidence": min(0.8, trigger_rate),
                        })

                # 2) 同时收紧该维度的检测阈值
                tighten_adjs = self._suggest_dimension_tighten(p)
                if tighten_adjs:
                    adjustments.extend(tighten_adjs)

            # ── 概率模型过度乐观 → 减小系数, 增大惩罚 ──
            elif ptype == "fp_probability_overestimate":
                overest = p.get("overestimate", 20)
                # 减小 base_coeff
                reduction = min(0.1, overest / 500)
                new_coeff = max(0.3, MODEL_PARAMS.prob_base_coeff - reduction)
                if new_coeff < MODEL_PARAMS.prob_base_coeff:
                    adjustments.append({
                        "param": "prob_base_coeff",
                        "target": "model_params",
                        "direction": "tighten",
                        "old": MODEL_PARAMS.prob_base_coeff,
                        "new": round(new_coeff, 3),
                        "reason": f"概率偏差{overest:.0f}%, 基础系数降{MODEL_PARAMS.prob_base_coeff}→{new_coeff:.3f}",
                        "confidence": 0.75,
                    })
                # 增大阶段加分的门槛 (减少 phase bonus)
                new_bonus = max(5, MODEL_PARAMS.prob_phase_bonus - 3)
                if new_bonus < MODEL_PARAMS.prob_phase_bonus:
                    adjustments.append({
                        "param": "prob_phase_bonus",
                        "target": "model_params",
                        "direction": "tighten",
                        "old": MODEL_PARAMS.prob_phase_bonus,
                        "new": new_bonus,
                        "reason": f"阶段加分降{MODEL_PARAMS.prob_phase_bonus}→{new_bonus}",
                        "confidence": 0.7,
                    })

            # ── 系统性偏差 (误报后下跌) → 增大超买/已涨惩罚 ──
            elif ptype == "fp_systematic_bias":
                new_penalty = min(25, MODEL_PARAMS.prob_overbought_penalty + 5)
                adjustments.append({
                    "param": "prob_overbought_penalty",
                    "target": "model_params",
                    "direction": "tighten",
                    "old": MODEL_PARAMS.prob_overbought_penalty,
                    "new": new_penalty,
                    "reason": f"误报后常下跌, 超买惩罚增至{new_penalty}",
                    "confidence": 0.8,
                })
                new_pump_penalty = min(20, MODEL_PARAMS.prob_already_pumped_penalty + 3)
                adjustments.append({
                    "param": "prob_already_pumped_penalty",
                    "target": "model_params",
                    "direction": "tighten",
                    "old": MODEL_PARAMS.prob_already_pumped_penalty,
                    "new": new_pump_penalty,
                    "reason": f"已涨惩罚增至{new_pump_penalty}",
                    "confidence": 0.75,
                })

            # ── 阶段判定错误 → 提高阶段门槛分 ──
            elif ptype == "fp_phase_error":
                new_high = min(85, MODEL_PARAMS.phase_high_score + 3)
                if new_high > MODEL_PARAMS.phase_high_score:
                    adjustments.append({
                        "param": "phase_high_score",
                        "target": "model_params",
                        "direction": "tighten",
                        "old": MODEL_PARAMS.phase_high_score,
                        "new": new_high,
                        "reason": f"阶段'{p.get('phase','')}'误报率高, 门槛{MODEL_PARAMS.phase_high_score}→{new_high}",
                        "confidence": 0.65,
                    })

        return adjustments

    def _suggest_dimension_tighten(self, pattern: Dict) -> List[Dict]:
        """收紧某个维度的阈值 (与 _suggest_dimension_loosen 反向)"""
        dim = pattern.get("dimension", "").lower()
        th = self._current_thresholds
        adjs = []

        # 反向映射: 收紧 = loosen 的反方向
        mapping = {
            "缩量横盘": [("vol_shrink_high", th.vol_shrink_high, 0.9, "lower", "缩量阈值从{old:.2f}收紧到{new:.2f}")],
            "大单占比": [("large_order_high", th.large_order_high, 1.1, "higher", "大单阈值从{old:.0f}%提高到{new:.0f}%")],
            "买卖不平衡": [("imbalance_high", th.imbalance_high, 1.05, "higher", "买盘偏向阈值从{old:.2f}提高到{new:.2f}")],
            "链上净流出": [("net_outflow_high", th.net_outflow_high, 1.15, "higher", "链上净流出阈值从{old}笔提高到{new}笔")],
        }

        params = mapping.get(dim, [])
        for param_name, old_val, factor, direction, reason_tpl in params:
            if direction == "higher":
                new_val = old_val * min(factor, 1 + self.MAX_ADJUST_PCT)
            else:
                new_val = old_val * max(factor, 1 - self.MAX_ADJUST_PCT)
            if isinstance(old_val, int):
                new_val = int(round(new_val))
            new_val = round(new_val, 4) if isinstance(new_val, float) else new_val
            if new_val != old_val:
                adjs.append({
                    "param": param_name,
                    "target": "thresholds",
                    "direction": "tighten",
                    "old": old_val,
                    "new": new_val,
                    "reason": reason_tpl.format(old=old_val, new=new_val),
                    "confidence": 0.6,
                })
        return adjs

    # ── 冲突仲裁 ─────────────────────────────────────────────────────────

    def _resolve_conflicts(self, adjustments: List[Dict], stats: Dict) -> List[Dict]:
        """
        当漏报说"放宽"而误报说"收紧"同一参数时, 根据精确率/召回率权衡:
        - 召回率 < 50% → 优先放宽 (漏报太多)
        - 精确率 < 50% → 优先收紧 (误报太多)
        - 都不低 → 取消冲突的调整, 保守不动
        """
        by_param = defaultdict(list)
        for a in adjustments:
            by_param[a["param"]].append(a)

        resolved = []
        precision = stats.get("precision", 50)
        recall = stats.get("recall", 50)

        for param, adjs in by_param.items():
            directions = set(a.get("direction", "loosen") for a in adjs)

            if len(directions) <= 1:
                # 无冲突, 取最高置信度的
                best = max(adjs, key=lambda a: a.get("confidence", 0))
                resolved.append(best)
            else:
                # 冲突! 根据精确率/召回率仲裁
                loosen = [a for a in adjs if a.get("direction") != "tighten"]
                tighten = [a for a in adjs if a.get("direction") == "tighten"]

                if recall < 40 and loosen:
                    # 召回率太低, 优先放宽
                    resolved.append(max(loosen, key=lambda a: a.get("confidence", 0)))
                    logger.info(f"[Learning] 冲突仲裁 {param}: 召回率{recall:.0f}%低, 优先放宽")
                elif precision < 40 and tighten:
                    # 精确率太低, 优先收紧
                    resolved.append(max(tighten, key=lambda a: a.get("confidence", 0)))
                    logger.info(f"[Learning] 冲突仲裁 {param}: 精确率{precision:.0f}%低, 优先收紧")
                else:
                    # 都还行, 保守不动
                    logger.info(f"[Learning] 冲突仲裁 {param}: 精确率{precision:.0f}%/召回率{recall:.0f}%, 保持不变")

        return resolved
        self._learning_history.append(lesson)

        logger.warning(f"📚 [Learning] 学习完成: {lesson.summary}")
        return lesson

    # ── 2. 分析漏报的共性模式 ────────────────────────────────────────────

    def _analyze_missed_patterns(self, missed_events: List[Dict]) -> List[Dict]:
        """
        分析所有漏报事件, 找出爆涨前的共性特征

        每个漏报事件包含 pre_pump_score, pre_pump_signals_json, lookback_json
        我们提取:
        - 哪些维度评分为 0 (完全未触发)
        - 各维度的原始值分布 (是否刚好在阈值边缘)
        - 爆涨前的成交量/价格形态特征
        """
        patterns = []

        # 收集所有漏报的指标数据
        dim_scores = defaultdict(list)       # 各维度得分
        dim_raw_values = defaultdict(list)   # 各维度原始值
        pre_scores = []
        pre_phases = Counter()
        symbols = []

        for event in missed_events:
            symbols.append(event["symbol"])
            pre_scores.append(event.get("pre_pump_score", 0))
            pre_phases[event.get("pre_pump_phase", "")] += 1

            # 解析爆涨前的信号
            try:
                raw = event.get("pre_pump_signals_json", "[]")
                signals = json.loads(raw) if isinstance(raw, str) else raw
                # 如果 signals 是字符串列表 (双重编码), 再解析一层
                if signals and isinstance(signals[0], str):
                    signals = [json.loads(s) if isinstance(s, str) else s for s in signals]
                # 确保每个 signal 是 dict
                signals = [s for s in signals if isinstance(s, dict)]
            except (json.JSONDecodeError, TypeError, IndexError):
                signals = []

            signal_dims = {s.get("dimension", ""): s for s in signals}

            # 解析回溯快照中的指标
            try:
                lookback = json.loads(event.get("lookback_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                lookback = []

            # 从快照提取各维度的metrics
            for snap in lookback[:5]:  # 最近 5 个快照
                try:
                    raw_metrics = snap.get("metrics_json", "{}")
                    metrics = json.loads(raw_metrics) if isinstance(raw_metrics, str) else raw_metrics
                    if not isinstance(metrics, dict):
                        metrics = {}
                except (json.JSONDecodeError, TypeError):
                    metrics = {}
                for dim_name, dim_val in metrics.items():
                    if isinstance(dim_val, (int, float)):
                        dim_raw_values[dim_name].append(dim_val)

            # 记录哪些维度有触发、哪些为 0
            all_dims = ["accumulation", "large_orders", "imbalance",
                        "onchain_flow", "wash_trade", "concentration", "spread"]
            for dim in all_dims:
                score = 0
                for s in signals:
                    if dim.replace("_", "") in s.get("dimension", "").lower().replace(" ", ""):
                        score = s.get("score", 0)
                        break
                dim_scores[dim].append(score)

        # ── 发现 1: 哪些维度在漏报时完全未触发 ──
        for dim, scores in dim_scores.items():
            zero_pct = sum(1 for s in scores if s == 0) / len(scores) * 100
            avg_score = sum(scores) / len(scores)

            if zero_pct >= 60:
                patterns.append({
                    "type": "silent_dimension",
                    "dimension": dim,
                    "zero_rate": zero_pct,
                    "avg_score": avg_score,
                    "finding": f"维度 [{dim}] 在 {zero_pct:.0f}% 的漏报中完全未触发 (均分 {avg_score:.1f})",
                    "impact": "high",
                })

        # ── 发现 2: 整体评分分布 (是否卡在阈值边缘) ──
        if pre_scores:
            avg_score = sum(pre_scores) / len(pre_scores)
            near_threshold = sum(1 for s in pre_scores if 50 <= s < 70) / len(pre_scores) * 100

            if near_threshold >= 40:
                patterns.append({
                    "type": "threshold_edge",
                    "finding": f"{near_threshold:.0f}% 的漏报评分在 50-70 区间 (均分 {avg_score:.1f}), 刚好低于预警阈值 70",
                    "avg_score": avg_score,
                    "near_threshold_pct": near_threshold,
                    "impact": "high",
                })

            if avg_score < 40:
                patterns.append({
                    "type": "low_score_miss",
                    "finding": f"漏报平均评分仅 {avg_score:.1f}, 模型可能缺少关键维度",
                    "avg_score": avg_score,
                    "impact": "critical",
                })

        # ── 发现 3: 阶段判定偏差 ──
        if pre_phases:
            most_common = pre_phases.most_common(1)[0]
            if most_common[0] in ("市场化交易", "轻度异常"):
                patterns.append({
                    "type": "phase_miss",
                    "finding": f"漏报中最常见阶段: \"{most_common[0]}\" ({most_common[1]}次), 阶段判定过于保守",
                    "phase_distribution": dict(pre_phases),
                    "impact": "medium",
                })

        # ── 发现 4: 各维度原始值是否在阈值附近 ──
        self._check_threshold_proximity(patterns, dim_raw_values)

        logger.info(f"[Learning] 发现 {len(patterns)} 个模式")
        return patterns

    def _check_threshold_proximity(self, patterns: List[Dict], raw_values: Dict):
        """检查原始指标值是否接近但未突破阈值"""
        th = self._current_thresholds

        checks = [
            ("vol_shrink_ratio", "accumulation", th.vol_shrink_high, "lower",
             "缩量比均值 {avg:.2f}, 阈值 {th:.2f}, 差 {gap:.2f}"),
            ("large_orders", "large_orders", th.large_order_high, "upper",
             "大单占比均值 {avg:.1f}%, 阈值 {th:.1f}%, 差 {gap:.1f}%"),
            ("imbalance", "imbalance", th.imbalance_high, "upper",
             "买盘偏向均值 {avg:.2f}, 阈值 {th:.2f}, 差 {gap:.2f}"),
        ]

        for key, dim, threshold, direction, template in checks:
            values = raw_values.get(key, []) or raw_values.get(dim, [])
            if not values:
                continue
            avg = sum(values) / len(values)

            if direction == "lower":
                gap = threshold - avg
                near = 0 < gap < threshold * 0.3
            else:
                gap = avg - threshold
                near = -threshold * 0.3 < gap < 0

            if near:
                patterns.append({
                    "type": "near_threshold",
                    "dimension": dim,
                    "finding": template.format(avg=avg, th=threshold, gap=abs(gap)),
                    "avg_value": avg,
                    "threshold": threshold,
                    "gap": abs(gap),
                    "impact": "high",
                })

    # ── 3. 生成调整建议 ──────────────────────────────────────────────────

    def _propose_adjustments(
        self, patterns: List[Dict], missed_events: List[Dict]
    ) -> List[Dict]:
        """根据发现的模式, 生成具体的阈值调整建议"""
        adjustments = []

        for p in patterns:
            ptype = p.get("type")

            # 维度完全静默 → 放宽该维度阈值
            if ptype == "silent_dimension":
                adj = self._suggest_dimension_loosen(p)
                if adj:
                    adjustments.extend(adj)

            # 评分卡在阈值边缘 → 降低预警阈值
            elif ptype == "threshold_edge":
                near_pct = p.get("near_threshold_pct", 0)
                avg_score = p.get("avg_score", 0)
                # 建议将预警阈值降到 漏报平均分 + 5 (但不低于50)
                new_threshold = max(50, int(avg_score) + 5)
                if new_threshold < self._current_alert_config.min_control_score:
                    adjustments.append({
                        "param": "alert_min_control_score",
                        "target": "alert_config",
                        "old": self._current_alert_config.min_control_score,
                        "new": new_threshold,
                        "reason": f"{near_pct:.0f}%漏报评分在50-70, 降低触发阈值到{new_threshold}",
                        "confidence": min(0.9, near_pct / 100),
                    })

            # 整体评分太低 → 需要增加新维度或放宽多个阈值
            elif ptype == "low_score_miss":
                adjustments.append({
                    "param": "alert_min_signal_count",
                    "target": "alert_config",
                    "old": self._current_alert_config.min_signal_count,
                    "new": max(2, self._current_alert_config.min_signal_count - 1),
                    "reason": f"漏报均分仅{p['avg_score']:.0f}, 降低信号数要求",
                    "confidence": 0.6,
                })

            # 原始值接近阈值 → 微调该阈值
            elif ptype == "near_threshold":
                adj = self._suggest_threshold_nudge(p)
                if adj:
                    adjustments.append(adj)

        # 去重 + 排优先级
        seen = set()
        unique = []
        for a in sorted(adjustments, key=lambda x: -x.get("confidence", 0)):
            key = a["param"]
            if key not in seen:
                seen.add(key)
                unique.append(a)

        return unique

    def _suggest_dimension_loosen(self, pattern: Dict) -> List[Dict]:
        """针对静默维度, 建议放宽其阈值"""
        dim = pattern.get("dimension", "")
        th = self._current_thresholds
        adjs = []

        mapping = {
            "accumulation": [
                ("vol_shrink_high", th.vol_shrink_high, 1.15, "higher",
                 "缩量阈值从{old:.2f}放宽到{new:.2f}"),
                ("price_range_medium", th.price_range_medium, 1.15, "higher",
                 "横盘振幅从{old:.2f}放宽到{new:.2f}"),
            ],
            "large_orders": [
                ("large_order_high", th.large_order_high, 0.85, "lower",
                 "大单阈值从{old:.0f}%降到{new:.0f}%"),
            ],
            "imbalance": [
                ("imbalance_high", th.imbalance_high, 0.95, "lower",
                 "买盘偏向阈值从{old:.2f}降到{new:.2f}"),
            ],
            "onchain_flow": [
                ("net_outflow_high", th.net_outflow_high, 0.80, "lower",
                 "链上净流出阈值从{old}笔降到{new}笔"),
            ],
            "wash_trade": [
                ("wash_trade_high", th.wash_trade_high, 0.85, "lower",
                 "对倒阈值从{old:.0f}%降到{new:.0f}%"),
            ],
            "concentration": [
                ("top10_hold_critical", th.top10_hold_critical, 0.92, "lower",
                 "Top10持仓阈值从{old:.0f}%降到{new:.0f}%"),
                ("holder_decrease_high", th.holder_decrease_high, 0.80, "lower",
                 "地址减少阈值从{old}降到{new}"),
            ],
            "spread": [
                ("spread_narrow_critical", th.spread_narrow_critical, 1.20, "higher",
                 "价差阈值从{old:.3f}%放宽到{new:.3f}%"),
            ],
        }

        params = mapping.get(dim, [])
        for param_name, old_val, factor, direction, reason_tpl in params:
            if direction == "higher":
                new_val = old_val * min(factor, 1 + self.MAX_ADJUST_PCT)
            else:
                new_val = old_val * max(factor, 1 - self.MAX_ADJUST_PCT)

            # 整数参数保持整数
            if isinstance(old_val, int):
                new_val = int(round(new_val))

            if new_val != old_val:
                adjs.append({
                    "param": param_name,
                    "target": "thresholds",
                    "old": old_val,
                    "new": round(new_val, 4) if isinstance(new_val, float) else new_val,
                    "reason": reason_tpl.format(old=old_val, new=new_val),
                    "confidence": min(0.85, pattern.get("zero_rate", 50) / 100),
                })

        return adjs

    def _suggest_threshold_nudge(self, pattern: Dict) -> Optional[Dict]:
        """对接近阈值的参数做微调"""
        dim = pattern.get("dimension", "")
        avg_val = pattern.get("avg_value", 0)
        threshold = pattern.get("threshold", 0)
        gap = pattern.get("gap", 0)

        # 将阈值移动到 gap 的 60% 处 (不完全到 avg_val, 留缓冲)
        nudge = gap * 0.6

        th = self._current_thresholds
        param_map = {
            "accumulation": ("vol_shrink_high", th.vol_shrink_high, "higher"),
            "large_orders": ("large_order_high", th.large_order_high, "lower"),
            "imbalance": ("imbalance_high", th.imbalance_high, "lower"),
        }

        if dim not in param_map:
            return None

        param_name, old_val, direction = param_map[dim]

        if direction == "higher":
            new_val = old_val + nudge
        else:
            new_val = old_val - nudge

        # 安全边界
        max_change = old_val * self.MAX_ADJUST_PCT
        if abs(new_val - old_val) > max_change:
            new_val = old_val + max_change if direction == "higher" else old_val - max_change

        new_val = round(new_val, 4)
        if new_val == old_val:
            return None

        return {
            "param": param_name,
            "target": "thresholds",
            "old": old_val,
            "new": new_val,
            "reason": f"[{dim}] 漏报均值{avg_val:.3f}接近阈值{threshold:.3f}, 微调到{new_val:.3f}",
            "confidence": 0.7,
        }

    # ── 4. 应用调整 ──────────────────────────────────────────────────────

    def _apply_adjustments(self, lesson: LessonLearned):
        """
        实际修改运行中的参数

        支持三种 target:
        - thresholds: 检测阈值 (config.THRESHOLDS)
        - alert_config: 预警条件 (config.ALERT_CONFIG)
        - model_params: 模型权重/系数 (MODEL_PARAMS)
        """
        from analysis.model_params import MODEL_PARAMS, DimensionWeight

        for adj in lesson.adjustments:
            target = adj.get("target", "thresholds")
            param = adj["param"]
            new_val = adj["new"]

            if target == "thresholds":
                if hasattr(self._current_thresholds, param):
                    old_val = getattr(self._current_thresholds, param)
                    setattr(self._current_thresholds, param, new_val)
                    setattr(THRESHOLDS, param, new_val)
                    logger.info(f"[Learning] 阈值: {param} = {old_val} → {new_val}")

            elif target == "alert_config":
                clean_param = param.replace("alert_", "")
                if hasattr(self._current_alert_config, clean_param):
                    old_val = getattr(self._current_alert_config, clean_param)
                    setattr(self._current_alert_config, clean_param, new_val)
                    setattr(ALERT_CONFIG, clean_param, new_val)
                    logger.info(f"[Learning] 预警: {clean_param} = {old_val} → {new_val}")

            elif target == "model_params":
                # 处理嵌套属性, e.g. "w_accumulation.critical_score"
                if "." in param:
                    obj_name, attr_name = param.split(".", 1)
                    if hasattr(MODEL_PARAMS, obj_name):
                        obj = getattr(MODEL_PARAMS, obj_name)
                        if hasattr(obj, attr_name):
                            old_val = getattr(obj, attr_name)
                            setattr(obj, attr_name, new_val)
                            logger.info(f"[Learning] 模型: {param} = {old_val} → {new_val}")
                else:
                    # 顶层属性, e.g. "prob_base_coeff"
                    if hasattr(MODEL_PARAMS, param):
                        old_val = getattr(MODEL_PARAMS, param)
                        setattr(MODEL_PARAMS, param, new_val)
                        logger.info(f"[Learning] 模型: {param} = {old_val} → {new_val}")

    # ── 5. 构建学习报告 ──────────────────────────────────────────────────

    def _build_lesson(
        self, missed: List[Dict], patterns: List[Dict],
        adjustments: List[Dict], stats: Dict
    ) -> LessonLearned:
        """构建完整的学习报告"""
        symbols = list(set(e["symbol"] for e in missed))

        # 估算新命中率
        near_threshold_misses = sum(
            1 for e in missed if 50 <= e.get("pre_pump_score", 0) < 70
        )
        estimated_new_hits = stats["predicted"] + near_threshold_misses * 0.6
        estimated_hit_rate = (
            estimated_new_hits / stats["total_pumps"] * 100
            if stats["total_pumps"] > 0 else 0
        )

        summary_parts = [f"分析{len(missed)}次漏报"]
        for adj in adjustments[:3]:
            summary_parts.append(adj["reason"])

        lesson = LessonLearned(
            lesson_id=f"L{int(time.time())}",
            timestamp=int(time.time() * 1000),
            event_type="missed_pump_analysis",
            summary=" | ".join(summary_parts),
            missed_count=len(missed),
            symbols=symbols,
            patterns=patterns,
            old_thresholds=asdict(self._current_thresholds),
            new_thresholds={},  # 调整后填入
            adjustments=adjustments,
            estimated_new_hit_rate=estimated_hit_rate,
            estimated_false_positive_increase=len(adjustments) * 2,  # 粗估
        )

        return lesson

    def _save_lesson(self, lesson: LessonLearned):
        """持久化学习记录"""
        with self.store._conn() as conn:
            conn.execute("""
                INSERT INTO learning_log
                (timestamp, event_type, summary, old_thresholds_json,
                 new_thresholds_json, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                lesson.timestamp,
                lesson.event_type,
                lesson.summary,
                json.dumps(lesson.old_thresholds, ensure_ascii=False),
                json.dumps(lesson.new_thresholds, ensure_ascii=False),
                json.dumps(lesson.to_dict(), ensure_ascii=False),
            ))

    # ── 获取当前状态 ─────────────────────────────────────────────────────

    def get_current_thresholds(self) -> Dict:
        return asdict(self._current_thresholds)

    def get_learning_history(self, limit: int = 20) -> List[Dict]:
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM learning_log ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_status_report(self, days: int = 30) -> str:
        """生成学习模块状态报告"""
        stats = self.pump_monitor.get_full_stats(days)
        history = self.get_learning_history(5)

        lines = [
            "📚 自学习模块状态报告",
            "━━━━━━━━━━━━━━━━━━━",
            f"📊 {days}天统计:",
            f"  总爆涨: {stats['total_pumps']}  |  总预警: {stats['total_alerts']}",
            f"  ✅ 命中: {stats['predicted']}  |  ❌ 漏报: {stats['missed']}  |  🔕 误报: {stats['false_positives']}",
            f"  🎯 精确率: {stats['precision']:.1f}% (预警→真涨)",
            f"  📡 召回率: {stats['recall']:.1f}% (真涨→预警)",
            f"  漏报均分: {stats['avg_missed_score']:.1f}  |  误报均分: {stats['avg_fp_score']:.1f}",
            f"  误报后实际涨跌: {stats['avg_fp_actual_change']:+.1f}%",
            "",
            f"🔧 学习次数: {len(self._learning_history)}",
        ]

        if history:
            lines.append("")
            lines.append("── 最近学习记录 ──")
            for h in history[:3]:
                lines.append(f"  {h.get('summary', '')[:80]}")

        lines.append("")
        lines.append("── 当前阈值 (已调整) ──")
        th = self._current_thresholds
        ac = self._current_alert_config
        lines.append(f"  缩量(high): {th.vol_shrink_high:.2f}  |  大单(high): {th.large_order_high:.0f}%")
        lines.append(f"  买盘(high): {th.imbalance_high:.2f}  |  链上(high): {th.net_outflow_high}")
        lines.append(f"  预警阈值: ≥{ac.min_control_score}分, ≥{ac.min_signal_count}维度, ≥{ac.min_pump_probability}%概率")

        return "\n".join(lines)

    # ── 恢复上次学习的阈值 ───────────────────────────────────────────────

    def restore_learned_thresholds(self):
        """启动时从 DB 恢复上次学习后的阈值"""
        history = self.get_learning_history(1)
        if not history:
            return

        last = history[0]
        try:
            details = json.loads(last.get("details_json", "{}"))
            adjustments = details.get("adjustments", [])
        except (json.JSONDecodeError, TypeError):
            return

        for adj in adjustments:
            param = adj["param"]
            new_val = adj["new"]
            target = adj.get("target", "thresholds")

            if target == "thresholds" and hasattr(THRESHOLDS, param):
                setattr(THRESHOLDS, param, new_val)
                setattr(self._current_thresholds, param, new_val)
                logger.info(f"[Learning] 恢复阈值: {param} = {new_val}")
            elif target == "alert_config":
                clean = param.replace("alert_", "")
                if hasattr(ALERT_CONFIG, clean):
                    setattr(ALERT_CONFIG, clean, new_val)
                    setattr(self._current_alert_config, clean, new_val)
                    logger.info(f"[Learning] 恢复配置: {clean} = {new_val}")
