"""
ML 预测层 (ML Predictor)

架构:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  数据采集 → 7维规则评分(可解释) → ML概率校正(可学习) → 预警决策
                                         ↑
                                历史暴涨/未涨样本自动回流训练
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

两层预测器:
  1. GBDTPredictor (LightGBM) — 当前主力, 少样本友好, 可解释
  2. NNPredictor (PyTorch MLP) — 预留接口, 数据充足后可切换

统一接口 BasePredictor:
  - extract_features(indicators, whale_analysis, onchain) → feature_vector
  - predict(features) → probability
  - train(samples) → metrics
  - save/load model
"""
import json
import time
import math
import pickle
import logging
import os
import numpy as np
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 特征工程
# ═══════════════════════════════════════════════════════════════════════════

# 完整特征列表 (42维)
FEATURE_NAMES = [
    # ── 规则引擎输出 (9维得分) ──
    "dim_accumulation",     # 缩量横盘得分
    "dim_large_orders",     # 大单占比得分
    "dim_imbalance",        # 买卖不平衡得分
    "dim_onchain_flow",     # 链上净流出得分
    "dim_wash_trade",       # 对倒交易得分
    "dim_concentration",    # 筹码集中得分
    "dim_spread",           # 价差异常得分
    "dim_funding_rate",     # 资金费率得分
    "dim_momentum",         # 动量构建得分
    "control_score",        # 规则引擎总分
    "rule_pump_prob",       # 规则引擎概率

    # ── 原始技术指标 ──
    "vol_shrink_ratio",     # 缩量比
    "vol_spike_ratio",      # 放量比
    "price_range_30d",      # 30日振幅
    "price_range_7d",       # 7日振幅
    "change_24h",           # 24h涨跌幅
    "change_7d",            # 7日涨跌幅
    "taker_buy_ratio_7d",   # 7日主动买入比
    "taker_buy_ratio_24h",  # 24h主动买入比
    "avg_trade_size_ratio", # 均笔放大倍数
    "rsi_14",               # RSI
    "bb_width",             # 布林带宽度
    "macd_histogram",       # MACD柱状
    "ma_alignment",         # 均线排列 (1=多头, -1=空头, 0=无序)

    # ── 链上数据 ──
    "net_outflow_count",    # 净流出笔数
    "top10_holders_pct",    # Top10持仓%
    "holder_change_7d",     # 持仓地址变化

    # ── 交叉特征 ──
    "vol_shrink_x_range",   # 缩量×振幅 (越小越像吸筹)
    "buy_ratio_x_outflow",  # 买入比×流出 (双重确认)
    "score_x_bb_squeeze",   # 控盘分×布林带窄度
    "rsi_x_change7d",       # RSI×7日涨幅 (超买+已涨=危险)
    "concentration_x_vol",  # 集中度×缩量 (高集中+缩量=控盘)

    # ── 动量特征 ──
    "score_delta_3scan",    # control_score change over last 3 scans
    "score_delta_6scan",    # control_score change over last 6 scans
    "price_momentum_3h",    # approximate 3h price momentum
    "volume_acceleration",  # volume change rate

    # ── 历史模式 ──
    "was_pumped_7d",        # 1 if token pumped in last 7 days
    "days_since_last_pump", # days since last pump (capped at 30)

    # ── 市场背景 ──
    "btc_change_24h",       # BTC 24h change
    "btc_rsi",              # BTC RSI

    # ── 市值层级 ──
    "log_market_cap_tier",  # log market cap tier (1=small, 2=mid, 3=large)

    # ── 时间特征 ──
    "hour_sin",             # hour of day (sine encoding)
    "hour_cos",             # hour of day (cosine encoding)
    "is_asia_session",      # 1 if Asia trading session

    # ── 实时特征 (从 realtime_engine) ──
    "rt_volume_surge_5m",   # 5分钟成交量突增
    "rt_price_change_5m",   # 5分钟价格变化
    "rt_bid_ask_imbalance", # 买卖盘不平衡 (实时)
]

NUM_FEATURES = 47


def extract_features(
    indicators,       # IndicatorResult
    whale_analysis,   # WhaleAnalysis
    onchain=None,     # OnchainMetrics (optional)
    historical_scores: List[int] = None,
    btc_data: dict = None,
    pump_history: dict = None,
    realtime_metrics: dict = None,  # realtime_engine metrics
) -> np.ndarray:
    """
    从各模块的输出中提取统一特征向量

    返回: shape=(41,) 的 numpy array
    """
    ind = indicators
    wa = whale_analysis

    # 均笔放大
    size_ratio = (ind.avg_trade_size_7d / ind.avg_trade_size_prev
                  if ind.avg_trade_size_prev > 0 else 1.0)

    # 均线排列: 1=多头(7>20>60), -1=空头(7<20<60), 0=无序
    if ind.sma7 > ind.sma20 > ind.sma60 > 0:
        ma_align = 1.0
    elif ind.sma7 < ind.sma20 < ind.sma60 and ind.sma60 > 0:
        ma_align = -1.0
    else:
        ma_align = 0.0

    # 链上
    net_outflow = 0.0
    top10 = 0.0
    holder_chg = 0.0
    if onchain:
        net_outflow = float(onchain.exchange_outflow_count - onchain.exchange_inflow_count)
        top10 = float(onchain.top10_holders_pct)
        holder_chg = float(onchain.holder_count_change_7d)

    # BB squeeze score: 越窄越高 (0-10)
    bb_squeeze = max(0, 10 - ind.bb_width) if ind.bb_width > 0 else 0

    # ── 新增: 动量特征 ──
    score_delta_3scan = 0.0
    score_delta_6scan = 0.0
    if historical_scores and len(historical_scores) >= 3:
        score_delta_3scan = float(historical_scores[0] - historical_scores[2])
    if historical_scores and len(historical_scores) >= 6:
        score_delta_6scan = float(historical_scores[0] - historical_scores[5])

    price_momentum_3h = float(ind.change_24h) / 8.0
    volume_acceleration = (ind.vol_spike_ratio - 1.0) * ind.vol_shrink_ratio

    # ── 新增: 历史模式 ──
    was_pumped_7d = float(pump_history.get("was_pumped_7d", False)) if pump_history else 0.0
    days_since_last_pump = min(30.0, pump_history.get("days_since_last", 30.0)) if pump_history else 30.0

    # ── 新增: 市场背景 ──
    btc_change_24h = btc_data.get("change_24h", 0.0) if btc_data else 0.0
    btc_rsi = btc_data.get("rsi", 50.0) if btc_data else 50.0

    # ── 新增: 实时特征 ──
    rt_volume_surge_5m = realtime_metrics.get("volume_surge_5m", 0.0) if realtime_metrics else 0.0
    rt_price_change_5m = realtime_metrics.get("price_change_5m", 0.0) if realtime_metrics else 0.0
    rt_bid_ask_imbalance = realtime_metrics.get("bid_ask_imbalance", 0.5) if realtime_metrics else 0.5

    # ── 新增: 市值层级 ──
    # Estimate market cap tier from 24h volume (rough approximation)
    # Small cap: <10M, Mid cap: 10M-500M, Large cap: >500M
    vol_24h = wa.volume_24h if hasattr(wa, 'volume_24h') else ind.vol_current
    if vol_24h < 10_000_000:
        market_cap_tier = 1.0  # small cap
    elif vol_24h < 500_000_000:
        market_cap_tier = 2.0  # mid cap
    else:
        market_cap_tier = 3.0  # large cap
    log_market_cap_tier = float(market_cap_tier)

    # ── 新增: 时间特征 ──
    import datetime
    utc_now = datetime.datetime.utcnow()
    hour = utc_now.hour
    # Sine/cosine encoding for cyclical hour feature
    hour_sin = float(np.sin(2 * np.pi * hour / 24))
    hour_cos = float(np.cos(2 * np.pi * hour / 24))
    # Asia session: 00:00-08:00 UTC
    is_asia_session = 1.0 if 0 <= hour < 8 else 0.0

    features = np.array([
        # 规则引擎输出
        float(wa.dim_accumulation),
        float(wa.dim_large_orders),
        float(wa.dim_imbalance),
        float(wa.dim_onchain_flow),
        float(wa.dim_wash_trade),
        float(wa.dim_concentration),
        float(wa.dim_spread),
        float(wa.dim_funding_rate),
        float(wa.dim_momentum),
        float(wa.control_score),
        float(wa.pump_probability),

        # 原始指标
        ind.vol_shrink_ratio,
        ind.vol_spike_ratio,
        ind.price_range_30d,
        ind.price_range_7d,
        ind.change_24h,
        ind.change_7d,
        ind.taker_buy_ratio_7d,
        ind.taker_buy_ratio_24h,
        size_ratio,
        ind.rsi_14,
        ind.bb_width,
        ind.macd_histogram,
        ma_align,

        # 链上
        net_outflow,
        top10,
        holder_chg,

        # 交叉特征
        ind.vol_shrink_ratio * ind.price_range_30d,                   # 缩量×振幅
        ind.taker_buy_ratio_7d * max(0, net_outflow / 100),           # 买入比×流出
        float(wa.control_score) * bb_squeeze / 100,                    # 控盘分×BB窄度
        ind.rsi_14 * max(0, ind.change_7d) / 100,                     # RSI×7日涨幅
        top10 * ind.vol_shrink_ratio / 100 if top10 > 0 else 0,       # 集中度×缩量

        # 动量特征
        score_delta_3scan,
        score_delta_6scan,
        price_momentum_3h,
        volume_acceleration,

        # 历史模式
        was_pumped_7d,
        days_since_last_pump,

        # 市场背景
        float(btc_change_24h),
        float(btc_rsi),

        # 市值层级
        log_market_cap_tier,

        # 时间特征
        hour_sin,
        hour_cos,
        is_asia_session,

        # 实时特征
        float(rt_volume_surge_5m),
        float(rt_price_change_5m),
        float(rt_bid_ask_imbalance),
    ], dtype=np.float32)

    # 处理 NaN/Inf
    features = np.nan_to_num(features, nan=0.0, posinf=100.0, neginf=-100.0)
    return features


# ═══════════════════════════════════════════════════════════════════════════
# 训练样本
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TrainingSample:
    """一个训练样本"""
    symbol: str
    timestamp: int
    features: np.ndarray       # shape=(38,)
    label: int                 # 1=24h内涨≥10%, 0=未涨 (lowered from 30% — 30% produced 1 positive in 30 days)
    actual_change: float       # 24h实际涨跌幅
    weight: float = 1.0        # 样本权重 (正样本加权)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "features": self.features.tolist(),
            "label": self.label,
            "actual_change": self.actual_change,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "TrainingSample":
        return cls(
            symbol=d["symbol"],
            timestamp=d["timestamp"],
            features=np.array(d["features"], dtype=np.float32),
            label=d["label"],
            actual_change=d["actual_change"],
            weight=d.get("weight", 1.0),
        )


# ═══════════════════════════════════════════════════════════════════════════
# 抽象接口
# ═══════════════════════════════════════════════════════════════════════════

class BasePredictor(ABC):
    """
    预测器统一接口

    所有预测器 (GBDT / NN / 未来任何模型) 都实现这个接口,
    上层代码只调用接口方法, 切换模型无需改业务逻辑
    """

    @abstractmethod
    def predict(self, features: np.ndarray):
        """
        预测 24h 内涨≥50% 的概率

        Args:
            features: shape=(38,) 特征向量
        Returns:
            (probability, confidence) tuple or None
        """
        ...

    @abstractmethod
    def predict_batch(self, features: np.ndarray) -> np.ndarray:
        """
        批量预测

        Args:
            features: shape=(N, 38)
        Returns:
            shape=(N,) 概率数组
        """
        ...

    @abstractmethod
    def train(self, samples: List[TrainingSample]) -> Dict:
        """
        用历史样本训练/更新模型

        Returns:
            {"auc": float, "precision": float, "recall": float,
             "n_samples": int, "n_positive": int, ...}
        """
        ...

    @abstractmethod
    def save(self, path: str):
        """持久化模型"""
        ...

    @abstractmethod
    def load(self, path: str) -> bool:
        """
        加载模型
        Returns: True=加载成功, False=无模型文件(使用规则引擎回退)
        """
        ...

    @abstractmethod
    def is_ready(self) -> bool:
        """模型是否已训练好可用"""
        ...

    @abstractmethod
    def feature_importance(self) -> Dict[str, float]:
        """返回特征重要性 (用于可解释性)"""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# GBDT 预测器 (LightGBM)
# ═══════════════════════════════════════════════════════════════════════════

class GBDTPredictor(BasePredictor):
    """
    基于 LightGBM 的梯度提升树预测器 (集成版本)

    优点:
    - 少样本友好 (50+ 正样本即可开始训练)
    - 内置特征重要性 (可解释)
    - 支持 scale_pos_weight 处理极端不平衡
    - 训练快 (<1s)
    - 集成3个子模型: 全量/近期/高置信
    """

    _min_samples = 30

    def __init__(self):
        self._model = None
        self._model_recent = None
        self._model_highconf = None
        self._calibrator = None
        self._is_trained = False
        self._train_metrics: Dict = {}
        self._model_version = 0
        self._last_train_time = 0

    def predict(self, features: np.ndarray):
        if not self.is_ready():
            return None

        # Pad/trim features to match model
        if len(features) < NUM_FEATURES:
            features = np.concatenate([features, np.zeros(NUM_FEATURES - len(features))])
        elif len(features) > NUM_FEATURES:
            features = features[:NUM_FEATURES]

        X = features.reshape(1, -1)

        probs = []
        weights = []

        try:
            p_all = float(self._model.predict_proba(X)[0][1])
            probs.append(p_all)
            weights.append(0.4)
        except Exception as e:
            logger.error(f"[GBDT] primary predict error: {e}")
            return None

        if self._model_recent is not None:
            try:
                p_recent = float(self._model_recent.predict_proba(X)[0][1])
                probs.append(p_recent)
                weights.append(0.35)
            except Exception:
                pass

        if self._model_highconf is not None:
            try:
                p_hc = float(self._model_highconf.predict_proba(X)[0][1])
                probs.append(p_hc)
                weights.append(0.25)
            except Exception:
                pass

        total_w = sum(weights[:len(probs)])
        ensemble_prob = sum(p * w for p, w in zip(probs, weights)) / total_w
        calibrated = self._calibrate(ensemble_prob)

        # Confidence
        if len(probs) >= 2:
            spread = max(probs) - min(probs)
            if spread < 0.15 and calibrated > 0.7:
                confidence = "high"
            elif spread > 0.3 or 0.4 < calibrated < 0.6:
                confidence = "low"
            else:
                confidence = "medium"
        else:
            confidence = "medium"

        return calibrated, confidence

    def predict_batch(self, features: np.ndarray) -> np.ndarray:
        if not self.is_ready():
            return np.full(features.shape[0], -1.0)
        try:
            probs = self._model.predict_proba(features)[:, 1]
            return np.clip(probs, 0.0, 1.0)
        except Exception as e:
            logger.error(f"[GBDT] predict_batch error: {e}")
            return np.full(features.shape[0], -1.0)

    def _train_single(self, X, y, weights, class_weight=None):
        import lightgbm as lgb
        model = lgb.LGBMClassifier(
            n_estimators=100, max_depth=5, learning_rate=0.05,
            num_leaves=31, min_child_samples=5, subsample=0.8,
            colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.1,
            class_weight=class_weight,
            verbose=-1)
        model.fit(X, y, sample_weight=weights)
        return model

    def train(self, samples):
        if len(samples) < self._min_samples:
            return None

        # Prepare data
        X = np.array([s["features"] if isinstance(s, dict) else s.features for s in samples])
        y = np.array([s["label"] if isinstance(s, dict) else s.label for s in samples])

        # Count positive samples
        n_positive = int(y.sum())

        # Require at least 5 positive samples — with fewer, GBDT memorises them and
        # recall stays 0% on any unseen positive. (label threshold lowered to 10%
        # to accumulate positives faster; don't train until we have enough.)
        MIN_POSITIVE = 5
        if n_positive < MIN_POSITIVE:
            logger.info(
                f"[GBDT] Skipping training: only {n_positive} positive samples "
                f"(need ≥{MIN_POSITIVE}). Waiting for more 10%+ pump events."
            )
            return None

        # Handle feature dimension mismatch (old 30-feature samples vs new 38)
        if X.shape[1] < NUM_FEATURES:
            padding = np.zeros((X.shape[0], NUM_FEATURES - X.shape[1]))
            X = np.hstack([X, padding])
        elif X.shape[1] > NUM_FEATURES:
            X = X[:, :NUM_FEATURES]

        # Sample weights: recent 2x, pumps 3x
        weights = np.array([s.get("weight", 1.0) if isinstance(s, dict) else s.weight for s in samples])
        now = time.time() * 1000
        for i, s in enumerate(samples):
            ts = s.get("timestamp", 0) if isinstance(s, dict) else s.timestamp
            if now - ts < 14 * 86400 * 1000:
                weights[i] *= 2.0
            lbl = s.get("label") if isinstance(s, dict) else s.label
            if lbl == 1:
                weights[i] *= 3.0

        # Cold start: Use single model with balanced class weights when positive samples < 20
        if n_positive < 20:
            logger.info(f"[GBDT] Cold start mode: using single balanced model (n_positive={n_positive})")
            self._model = self._train_single(X, y, weights, class_weight='balanced')
            self._model_version += 1

            # Skip ensemble models in cold start mode
            self._model_recent = None
            self._model_highconf = None

            # Fit calibrator (Platt scaling)
            self._fit_calibrator(X, y)

            # Simple metrics
            try:
                from sklearn.metrics import roc_auc_score, precision_score, recall_score
                y_pred = self._model.predict_proba(X)[:, 1]
                auc = roc_auc_score(y, y_pred) if len(set(y)) > 1 else 0.5
                y_bin = (y_pred >= 0.5).astype(int)
                prec = precision_score(y, y_bin, zero_division=0)
                rec = recall_score(y, y_bin, zero_division=0)
            except Exception:
                auc, prec, rec = 0.5, 0.0, 0.0

            # Feature importance
            importance = self._model.feature_importances_
            top_idx = np.argsort(importance)[-10:][::-1]
            top_features = [
                (FEATURE_NAMES[i] if i < len(FEATURE_NAMES) else f"f{i}", float(importance[i]))
                for i in top_idx
            ]

            self._is_trained = True
            self._last_train_time = int(time.time())

            metrics = {
                "trained": True,
                "auc": auc,
                "precision": prec,
                "recall": rec,
                "cv_auc_mean": auc,
                "cv_auc_std": 0.0,
                "top_features": top_features,
                "samples": len(samples),
                "n_positive": n_positive,
                "cold_start": True,
            }
            self._train_metrics = metrics

            logger.warning(
                f"[GBDT] Cold start training complete v{self._model_version}: "
                f"AUC={auc:.3f} P={prec:.1%} R={rec:.1%} "
                f"({len(samples)} samples, {n_positive} positive)"
            )
            return metrics

        # Train primary model (all data)
        self._model = self._train_single(X, y, weights)
        self._model_version += 1

        # Train recent model (last 30 days)
        recent_mask = np.array([
            now - (s.get("timestamp", 0) if isinstance(s, dict) else s.timestamp) < 30 * 86400 * 1000
            for s in samples
        ])
        if recent_mask.sum() >= 20:
            self._model_recent = self._train_single(X[recent_mask], y[recent_mask], weights[recent_mask])

        # Train high-confidence model
        hc_mask = np.array([
            (s.get("weight", 1.0) if isinstance(s, dict) else s.weight) >= 3.0
            for s in samples
        ])
        if hc_mask.sum() >= 10:
            self._model_highconf = self._train_single(X[hc_mask], y[hc_mask], weights[hc_mask])

        # Fit calibrator (Platt scaling)
        self._fit_calibrator(X, y)

        # Cross-validation
        cv_auc = self._cross_validate(X, y, weights)

        # Feature importance
        importance = self._model.feature_importances_
        top_idx = np.argsort(importance)[-10:][::-1]
        top_features = [
            (FEATURE_NAMES[i] if i < len(FEATURE_NAMES) else f"f{i}", float(importance[i]))
            for i in top_idx
        ]

        # Metrics
        try:
            from sklearn.metrics import roc_auc_score, precision_score, recall_score
            y_pred = self._model.predict_proba(X)[:, 1]
            auc = roc_auc_score(y, y_pred) if len(set(y)) > 1 else 0.5
            y_bin = (y_pred >= 0.5).astype(int)
            prec = precision_score(y, y_bin, zero_division=0)
            rec = recall_score(y, y_bin, zero_division=0)
        except Exception:
            auc, prec, rec = 0.5, 0.0, 0.0

        self._is_trained = True
        self._last_train_time = int(time.time())

        metrics = {
            "trained": True,
            "auc": auc,
            "precision": prec,
            "recall": rec,
            "cv_auc_mean": cv_auc[0],
            "cv_auc_std": cv_auc[1],
            "top_features": top_features,
            "samples": len(samples),
        }
        self._train_metrics = metrics

        logger.warning(
            f"[GBDT] 训练完成 v{self._model_version}: "
            f"AUC={auc:.3f} P={prec:.1%} R={rec:.1%} "
            f"({len(samples)} samples)"
        )
        return metrics

    def _fit_calibrator(self, X, y):
        try:
            from sklearn.linear_model import LogisticRegression
            raw_probs = self._model.predict_proba(X)[:, 1].reshape(-1, 1)
            self._calibrator = LogisticRegression(C=1.0, solver='lbfgs')
            self._calibrator.fit(raw_probs, y)
        except Exception:
            self._calibrator = None

    def _calibrate(self, raw_prob):
        if hasattr(self, '_calibrator') and self._calibrator is not None:
            try:
                return float(self._calibrator.predict_proba([[raw_prob]])[0][1])
            except Exception:
                pass
        return raw_prob

    def _cross_validate(self, X, y, weights):
        try:
            from sklearn.model_selection import StratifiedKFold
            from sklearn.metrics import roc_auc_score
            if len(set(y)) < 2 or len(y) < 10:
                return (0.5, 0.0)
            kf = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
            aucs = []
            for train_idx, val_idx in kf.split(X, y):
                model = self._train_single(X[train_idx], y[train_idx], weights[train_idx])
                pred = model.predict_proba(X[val_idx])[:, 1]
                if len(set(y[val_idx])) > 1:
                    aucs.append(roc_auc_score(y[val_idx], pred))
            return (np.mean(aucs), np.std(aucs)) if aucs else (0.5, 0.0)
        except Exception:
            return (0.5, 0.0)

    def _calc_metrics(self, y_true: np.ndarray, y_pred: np.ndarray) -> Dict:
        """计算评估指标"""
        try:
            from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score
        except Exception:
            return {}
        metrics = {}
        try:
            if len(np.unique(y_true)) > 1:
                metrics["auc"] = round(roc_auc_score(y_true, y_pred), 4)
            else:
                metrics["auc"] = 0.0

            y_bin = (y_pred >= 0.5).astype(int)
            metrics["precision"] = round(precision_score(y_true, y_bin, zero_division=0), 4)
            metrics["recall"] = round(recall_score(y_true, y_bin, zero_division=0), 4)
            metrics["f1"] = round(f1_score(y_true, y_bin, zero_division=0), 4)

            k = max(1, int(y_true.sum()))
            top_k_idx = np.argsort(y_pred)[-k:]
            metrics["precision_at_k"] = round(y_true[top_k_idx].mean(), 4)
        except Exception as e:
            logger.error(f"[GBDT] metrics error: {e}")
        return metrics

    def save(self, path: str):
        if self._model is None:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        state = {
            "model": self._model,
            "model_recent": self._model_recent,
            "model_highconf": self._model_highconf,
            "calibrator": self._calibrator,
            "version": self._model_version,
            "metrics": self._train_metrics,
            "train_time": self._last_train_time,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)
        logger.info(f"[GBDT] 模型已保存: {path} (v{self._model_version})")

    def load(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                state = pickle.load(f)

            # Backward compatibility: old format used "model_str" (lgb.Booster)
            if "model_str" in state:
                import lightgbm as lgb
                # Old single-booster format — cannot use as LGBMClassifier,
                # discard and force retrain
                logger.warning("[GBDT] Old model format detected, will retrain on next cycle")
                return False

            self._model = state.get("model")
            self._model_recent = state.get("model_recent")
            self._model_highconf = state.get("model_highconf")
            self._calibrator = state.get("calibrator")
            self._model_version = state.get("version", 1)
            self._train_metrics = state.get("metrics", {})
            self._last_train_time = state.get("train_time", 0)
            self._is_trained = self._model is not None
            logger.info(f"[GBDT] 模型已加载: {path} (v{self._model_version})")
            return self._is_trained
        except Exception as e:
            logger.error(f"[GBDT] 加载失败: {e}")
            return False

    def is_ready(self) -> bool:
        return self._is_trained and self._model is not None

    def feature_importance(self) -> Dict[str, float]:
        if not self._is_trained or self._model is None:
            return {}
        try:
            imp = self._model.feature_importances_
            total = imp.sum() or 1
            return {name: round(float(val / total), 4)
                    for name, val in zip(FEATURE_NAMES, imp)}
        except Exception:
            return {}

    def get_status(self) -> Dict:
        return {
            "type": "GBDT",
            "ready": self.is_ready(),
            "version": self._model_version,
            "metrics": self._train_metrics,
            "last_train": self._last_train_time,
        }


# ═══════════════════════════════════════════════════════════════════════════
# NN 预测器 (PyTorch MLP) — 预留接口
# ═══════════════════════════════════════════════════════════════════════════

class NNPredictor(BasePredictor):
    """
    基于 PyTorch 的多层感知机预测器

    架构: Input(38) → Dense(64,ReLU) → Dropout(0.3) → Dense(32,ReLU)
          → Dropout(0.2) → Dense(1,Sigmoid)

    使用条件:
    - 需要 ≥500 正样本 (暴涨案例)
    - 需要 ≥5000 总样本
    - 需要 PyTorch 安装

    当前状态: 接口完整, 训练逻辑完整, 等待数据积累
    """

    MIN_SAMPLES = 2000
    MIN_POSITIVE_SAMPLES = 50

    def __init__(self, hidden_dims: List[int] = None):
        self._model = None
        self._scaler = None  # StandardScaler
        self._is_trained = False
        self._train_metrics: Dict = {}
        self._model_version = 0
        self._last_train_time = 0
        self._hidden_dims = hidden_dims or [64, 32]

    def _build_model(self):
        """构建 MLP"""
        import torch
        import torch.nn as nn

        layers = []
        in_dim = NUM_FEATURES
        for h_dim in self._hidden_dims:
            layers.extend([
                nn.Linear(in_dim, h_dim),
                nn.ReLU(),
                nn.BatchNorm1d(h_dim),
                nn.Dropout(0.3),
            ])
            in_dim = h_dim
        layers.append(nn.Linear(in_dim, 1))
        layers.append(nn.Sigmoid())

        return nn.Sequential(*layers)

    def predict(self, features: np.ndarray):
        if not self._is_trained or self._model is None:
            return None
        try:
            import torch
            self._model.eval()
            x = self._normalize(features.reshape(1, -1))
            x_t = torch.FloatTensor(x)
            with torch.no_grad():
                prob = self._model(x_t).item()
            return float(np.clip(prob, 0.0, 1.0)), "medium"
        except Exception as e:
            logger.error(f"[NN] predict error: {e}")
            return None

    def predict_batch(self, features: np.ndarray) -> np.ndarray:
        if not self._is_trained or self._model is None:
            return np.full(features.shape[0], -1.0)
        try:
            import torch
            self._model.eval()
            x = self._normalize(features)
            x_t = torch.FloatTensor(x)
            with torch.no_grad():
                probs = self._model(x_t).squeeze().numpy()
            return np.clip(probs, 0.0, 1.0)
        except Exception as e:
            logger.error(f"[NN] predict_batch error: {e}")
            return np.full(features.shape[0], -1.0)

    def train(self, samples: List[TrainingSample]) -> Dict:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler

        if len(samples) < self.MIN_SAMPLES:
            return {"error": f"样本不足: {len(samples)} < {self.MIN_SAMPLES}", "trained": False}

        labels = np.array([s.label for s in samples])
        n_pos = int(labels.sum())
        if n_pos < self.MIN_POSITIVE_SAMPLES:
            return {"error": f"正样本不足: {n_pos} < {self.MIN_POSITIVE_SAMPLES}", "trained": False}

        X = np.array([s.features for s in samples], dtype=np.float32)
        y = labels.astype(np.float32)

        # 标准化
        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X)

        # 划分
        X_train, X_val, y_train, y_val = train_test_split(
            X_scaled, y, test_size=0.2, stratify=y, random_state=42)

        # 不平衡采样
        class_weights = np.where(y_train == 1, len(y_train) / max(1, 2 * n_pos),
                                                  len(y_train) / max(1, 2 * (len(y_train) - n_pos)))
        sampler = WeightedRandomSampler(
            torch.DoubleTensor(class_weights), len(y_train), replacement=True)

        train_ds = TensorDataset(torch.FloatTensor(X_train), torch.FloatTensor(y_train))
        val_ds = TensorDataset(torch.FloatTensor(X_val), torch.FloatTensor(y_val))
        train_loader = DataLoader(train_ds, batch_size=64, sampler=sampler)
        val_loader = DataLoader(val_ds, batch_size=256)

        # 构建模型
        self._model = self._build_model()
        optimizer = torch.optim.Adam(self._model.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
        criterion = nn.BCELoss()

        # 训练
        best_val_loss = float("inf")
        best_state = None
        patience = 15
        no_improve = 0

        for epoch in range(100):
            self._model.train()
            train_loss = 0
            for xb, yb in train_loader:
                pred = self._model(xb).squeeze()
                loss = criterion(pred, yb)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            # 验证
            self._model.eval()
            val_loss = 0
            val_preds = []
            val_labels = []
            with torch.no_grad():
                for xb, yb in val_loader:
                    pred = self._model(xb).squeeze()
                    val_loss += criterion(pred, yb).item()
                    val_preds.extend(pred.numpy())
                    val_labels.extend(yb.numpy())

            val_loss /= max(1, len(val_loader))
            scheduler.step(val_loss)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.clone() for k, v in self._model.state_dict().items()}
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    break

        # 恢复最佳权重
        if best_state:
            self._model.load_state_dict(best_state)

        # 评估
        val_pred_np = np.array(val_preds)
        val_label_np = np.array(val_labels)
        metrics = self._calc_metrics(val_label_np, val_pred_np)
        metrics.update({
            "n_samples": len(samples),
            "n_positive": n_pos,
            "epochs": epoch + 1,
            "best_val_loss": round(best_val_loss, 4),
            "trained": True,
        })

        self._is_trained = True
        self._train_metrics = metrics
        self._model_version += 1
        self._last_train_time = int(time.time())

        logger.warning(
            f"[NN] 训练完成 v{self._model_version}: "
            f"AUC={metrics.get('auc', 0):.3f} epochs={epoch+1}"
        )
        return metrics

    def _calc_metrics(self, y_true, y_pred) -> Dict:
        try:
            from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score
        except Exception:
            return {}
        metrics = {}
        try:
            if len(np.unique(y_true)) > 1:
                metrics["auc"] = round(roc_auc_score(y_true, y_pred), 4)
            y_bin = (y_pred >= 0.5).astype(int)
            metrics["precision"] = round(precision_score(y_true, y_bin, zero_division=0), 4)
            metrics["recall"] = round(recall_score(y_true, y_bin, zero_division=0), 4)
            metrics["f1"] = round(f1_score(y_true, y_bin, zero_division=0), 4)
        except Exception:
            pass
        return metrics

    def _normalize(self, X: np.ndarray) -> np.ndarray:
        if self._scaler is None:
            return X
        return self._scaler.transform(X)

    def save(self, path: str):
        if self._model is None:
            return
        import torch
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        state = {
            "model_state": self._model.state_dict(),
            "hidden_dims": self._hidden_dims,
            "scaler": self._scaler,
            "version": self._model_version,
            "metrics": self._train_metrics,
        }
        torch.save(state, path)

    def load(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            import torch
            state = torch.load(path, map_location="cpu")
            self._hidden_dims = state.get("hidden_dims", [64, 32])
            self._model = self._build_model()
            self._model.load_state_dict(state["model_state"])
            self._scaler = state.get("scaler")
            self._model_version = state.get("version", 1)
            self._train_metrics = state.get("metrics", {})
            self._is_trained = True
            return True
        except Exception as e:
            logger.error(f"[NN] 加载失败: {e}")
            return False

    def is_ready(self) -> bool:
        return self._is_trained and self._model is not None

    def feature_importance(self) -> Dict[str, float]:
        # NN 没有原生特征重要性, 用梯度近似
        return {}

    def get_status(self) -> Dict:
        return {
            "type": "NN",
            "ready": self.is_ready(),
            "version": self._model_version,
            "metrics": self._train_metrics,
            "hidden_dims": self._hidden_dims,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 预测管理器 — 统一调度, 自动回退
# ═══════════════════════════════════════════════════════════════════════════

class PredictionManager:
    """
    预测管理器: 统一管理规则引擎 + ML 模型

    决策逻辑:
    1. ML 模型已就绪 → 使用 ML 概率 (用规则引擎概率做 sanity check)
    2. ML 模型未就绪 → 回退到规则引擎概率

    自动训练:
    - 每 N 小时从 DB 收集样本, 自动重训练
    - 正样本: pump_events 中的暴涨记录
    - 负样本: 同期未暴涨的快照
    """

    RETRAIN_INTERVAL_HOURS = 12    # 每12小时检查是否需要重训练
    MODEL_DIR = "models"
    DIVERGENCE_THRESHOLD = 0.3     # ML与规则引擎概率差>30% → 取保守值

    def __init__(self, store, model_type: str = "gbdt"):
        self.store = store
        self._model_type = model_type

        # 初始化预测器
        if model_type == "nn":
            self.predictor: BasePredictor = NNPredictor()
            self._model_path = os.path.join(self.MODEL_DIR, "nn_predictor.pt")
        else:
            self.predictor: BasePredictor = GBDTPredictor()
            self._model_path = os.path.join(self.MODEL_DIR, "gbdt_predictor.pkl")

        # NN 备用 (预留)
        self._nn_predictor: Optional[NNPredictor] = None

        self._last_train_time = 0
        self._sample_cache: List[TrainingSample] = []

        # 加载已有模型
        self.predictor.load(self._model_path)
        self._init_tables()

    def _init_tables(self):
        with self.store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ml_training_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    timestamp INTEGER,
                    features_json TEXT,
                    label INTEGER,
                    actual_change REAL,
                    weight REAL DEFAULT 1.0,
                    created_at INTEGER,
                    entry_price REAL,
                    label_verified INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS ml_model_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_type TEXT,
                    version INTEGER,
                    metrics_json TEXT,
                    n_samples INTEGER,
                    trained_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS pending_ml_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    timestamp INTEGER,
                    features_json TEXT,
                    entry_price REAL,
                    created_at INTEGER,
                    labeled INTEGER DEFAULT 0
                );
            """)

    # ── 延迟标签系统 (P0-1: 修复循环标签) ────────────────────────────────

    def save_ml_sample_pending(self, symbol: str, features: np.ndarray,
                                entry_price: float, timestamp: int = 0):
        """
        保存待标签样本 - 标签将在24小时后通过前向价格验证生成

        这是 P0-1 修复循环标签问题的核心实现:
        - 样本记录时不带标签 (label=NULL)
        - 记录入场价格用于后续计算 actual_change
        - 24h 后通过 label_pending_samples() 验证并生成真实标签
        """
        import json
        if timestamp == 0:
            timestamp = int(time.time() * 1000)
        now = int(time.time() * 1000)

        with self.store._conn() as conn:
            conn.execute("""
                INSERT INTO pending_ml_samples
                (symbol, timestamp, features_json, entry_price, created_at, labeled)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (symbol, timestamp, json.dumps(features.tolist()), entry_price, now))

    def label_pending_samples(self, older_than_hours: int = 24) -> int:
        """
        为待标签样本生成真实标签 (前向价格验证)

        从 pending_ml_samples 表中找出 24h 前的样本:
        1. 获取当前价格
        2. 计算 actual_change = (current_price - entry_price) / entry_price
        3. 生成标签: label=1 if actual_change >= 0.10 (10%) else 0
           (lowered from 30%: only 1 positive sample in 30 days at 30% threshold)
        4. 将已标签样本移入 ml_training_samples 表

        Returns:
            int: 成功标签的样本数量
        """
        import json
        cutoff_time_ms = int((time.time() - older_than_hours * 3600) * 1000)
        labeled_count = 0

        with self.store._conn() as conn:
            # 获取所有需要标签的样本
            rows = conn.execute("""
                SELECT id, symbol, timestamp, features_json, entry_price
                FROM pending_ml_samples
                WHERE labeled = 0 AND timestamp < ?
            """, (cutoff_time_ms,)).fetchall()

            for row in rows:
                sample_id = row["id"]
                symbol = row["symbol"]
                timestamp = row["timestamp"]
                features = json.loads(row["features_json"])
                entry_price = row["entry_price"]

                # 获取当前价格 (从 indicators 或通过 API)
                # 这里简化处理 - 实际应该调用 Binance API 获取最新价格
                current_price = self._get_current_price(symbol)
                if current_price is None:
                    continue

                # 计算实际涨跌幅
                actual_change = (current_price - entry_price) / entry_price if entry_price > 0 else 0
                label = 1 if actual_change >= 0.10 else 0  # 10% threshold (was 30% — too rare)

                # Positive samples are rare; upweight them to counteract class imbalance
                weight = 4.0 if label == 1 else 1.0

                # 移入正式训练表
                now = int(time.time() * 1000)
                conn.execute("""
                    INSERT INTO ml_training_samples
                    (symbol, timestamp, features_json, label, actual_change, weight,
                     created_at, entry_price, label_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (symbol, timestamp, json.dumps(features), label, actual_change,
                     weight, now, entry_price))

                # 标记为已标签
                conn.execute("UPDATE pending_ml_samples SET labeled = 1 WHERE id = ?", (sample_id,))
                labeled_count += 1

        return labeled_count

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取代币当前价格 (用于标签验证)"""
        import httpx
        try:
            async def fetch():
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}"
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return float(data.get("price", 0))

            # 同步执行
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(fetch())
        except Exception as e:
            logger.warning(f"[ML] Failed to fetch price for {symbol}: {e}")
            return None

    # ── 预测 (对外接口) ──────────────────────────────────────────────────

    def predict(self, indicators, whale_analysis, onchain=None,
                historical_scores=None, btc_data=None, pump_history=None) -> Dict:
        """
        综合预测: ML + 规则引擎

        返回:
        {
            "final_prob": 0~100 (最终使用的概率),
            "confidence_level": "high"/"medium"/"low",
            "source": "ml" / "rule",
            "features": np.ndarray,
        }
        """
        features = extract_features(indicators, whale_analysis, onchain,
                                    historical_scores, btc_data, pump_history)

        if self.predictor.is_ready():
            result = self.predictor.predict(features)
            if result is not None:
                prob, confidence = result
                return {
                    "final_prob": int(prob * 100),
                    "confidence_level": confidence,
                    "source": "ml",
                    "features": features,
                }

        return {
            "final_prob": whale_analysis.pump_probability,
            "confidence_level": "low",
            "source": "rule",
            "features": features,
        }

    # ── 样本收集 ─────────────────────────────────────────────────────────

    def record_sample(self, symbol: str, features: np.ndarray, label: int,
                      actual_change: float, weight: float = 1.0):
        """记录一个训练样本到 DB"""
        now = int(time.time() * 1000)
        with self.store._conn() as conn:
            conn.execute("""
                INSERT INTO ml_training_samples
                (symbol, timestamp, features_json, label, actual_change, weight, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (symbol, now, json.dumps(features.tolist()), label,
                  actual_change, weight, now))

    def collect_training_data(self, days: int = 60) -> List[TrainingSample]:
        """
        从 DB 收集训练数据

        正样本: pump_events (暴涨前的快照)
        负样本: 同期未暴涨的快照 (下采样到正样本的 20 倍)
        """
        samples = []

        # 从 ml_training_samples 表
        since = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM ml_training_samples
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            """, (since,)).fetchall()

        for r in rows:
            try:
                features = np.array(json.loads(r["features_json"]), dtype=np.float32)
                # Accept old (30, 41, 42) or new (47) feature samples
                if len(features) in (30, 41, 42, NUM_FEATURES):
                    samples.append(TrainingSample(
                        symbol=r["symbol"],
                        timestamp=r["timestamp"],
                        features=features,
                        label=r["label"],
                        actual_change=r["actual_change"],
                        weight=r["weight"],
                    ))
            except (json.JSONDecodeError, ValueError):
                continue

        return samples

    # ── 自动训练 ─────────────────────────────────────────────────────────

    def maybe_retrain(self) -> Optional[Dict]:
        """
        检查是否需要重训练, 如果需要则执行

        触发条件:
        - 距上次训练超过 RETRAIN_INTERVAL_HOURS
        - 有足够的新样本
        """
        now = time.time()
        if now - self._last_train_time < self.RETRAIN_INTERVAL_HOURS * 3600:
            return None

        samples = self.collect_training_data(days=90)
        if not samples:
            return None

        # Convert TrainingSample objects to dicts for the new train() interface
        sample_dicts = [s.to_dict() for s in samples]
        metrics = self.predictor.train(sample_dicts)
        if metrics and metrics.get("trained"):
            self.predictor.save(self._model_path)
            self._last_train_time = now

            with self.store._conn() as conn:
                conn.execute("""
                    INSERT INTO ml_model_history
                    (model_type, version, metrics_json, n_samples, trained_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (self._model_type, metrics.get("samples", 0),
                      json.dumps(metrics), len(samples), int(now * 1000)))

        return metrics

    # ── NN 切换 ──────────────────────────────────────────────────────────

    def switch_to_nn(self) -> Dict:
        """
        切换到 NN 预测器 (当数据足够时)

        Returns:
            训练结果 or 错误信息
        """
        samples = self.collect_training_data(days=180)
        nn = NNPredictor()
        result = nn.train(samples)

        if result.get("trained"):
            self._nn_predictor = nn
            self.predictor = nn
            self._model_type = "nn"
            self._model_path = os.path.join(self.MODEL_DIR, "nn_predictor.pt")
            nn.save(self._model_path)
            logger.warning(f"[ML] 已切换到 NN 预测器")

        return result

    # ── 状态查询 ──────────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        samples = self.collect_training_data(days=90)
        n_pos = sum(1 for s in samples if s.label == 1)
        return {
            "active_model": self._model_type,
            "model_status": self.predictor.get_status() if hasattr(self.predictor, "get_status") else {},
            "total_samples": len(samples),
            "positive_samples": n_pos,
            "last_train": self._last_train_time,
            "can_switch_to_nn": (len(samples) >= NNPredictor.MIN_SAMPLES
                                 and n_pos >= NNPredictor.MIN_POSITIVE_SAMPLES),
        }
