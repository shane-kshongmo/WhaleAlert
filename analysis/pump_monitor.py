"""
爆涨监控器 (Pump Monitor)
实时检测代币是否发生了真实的爆涨事件, 作为"真相标签"

职责:
1. 每次扫描后, 检查每个代币是否满足"爆涨"定义
2. 记录爆涨事件 + 爆涨前的全部快照 (用于回溯学习)
3. 与预警记录对比, 标记 "命中" vs "漏报"
"""
import time
import json
import logging
from typing import Dict, List, Optional, Tuple
from collections import Counter
from dataclasses import dataclass, field

from data.data_store import DataStore

logger = logging.getLogger(__name__)


@dataclass
class PumpEvent:
    """一次真实的爆涨事件"""
    symbol: str
    detected_at: int            # 检测到爆涨的时间戳 (ms)
    pump_start_price: float     # 爆涨起始价格
    pump_peak_price: float      # 峰值价格
    pump_pct: float             # 涨幅百分比
    pump_duration_hours: float  # 从启动到峰值的时间 (小时)
    volume_surge_ratio: float   # 成交量放大倍数

    # 预警对比
    was_predicted: bool = False        # 是否在爆涨前发出过预警
    pre_pump_score: int = 0            # 爆涨前的控盘评分
    pre_pump_phase: str = ""           # 爆涨前的阶段判定
    pre_pump_signals: List[Dict] = field(default_factory=list)  # 爆涨前的信号

    # 回溯分析 (爆涨前 7 天的指标快照)
    lookback_snapshots: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "detected_at": self.detected_at,
            "pump_start_price": self.pump_start_price,
            "pump_peak_price": self.pump_peak_price,
            "pump_pct": self.pump_pct,
            "pump_duration_hours": self.pump_duration_hours,
            "volume_surge_ratio": self.volume_surge_ratio,
            "was_predicted": self.was_predicted,
            "pre_pump_score": self.pre_pump_score,
            "pre_pump_phase": self.pre_pump_phase,
            "pre_pump_signals": self.pre_pump_signals,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 爆涨定义 — 可调节
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PumpDefinition:
    """暴涨定义: 24h ≥30%"""
    min_pump_pct_24h: float = 30.0
    min_volume_surge: float = 1.5        # 量≥1.5x (30%不需要极端放量)
    max_btc_change_24h: float = 12.0     # BTC涨>12% → 大盘行情

@dataclass
class CrashDefinition:
    """暴跌定义: 4h 内跌幅 ≥50%"""
    min_crash_pct_4h: float = 50.0       # 4h 跌≥50%
    min_crash_pct_24h: float = 60.0      # 或24h 跌≥60% (慢速崩盘)
    min_volume_surge: float = 2.0        # 暴跌必然放量
    max_btc_change_24h: float = -15.0    # BTC跌>15% → 大盘暴跌 (注意是负数)

PUMP_DEF = PumpDefinition()
CRASH_DEF = CrashDefinition()


@dataclass
class CrashEvent:
    """一次暴跌事件"""
    symbol: str
    detected_at: int
    crash_start_price: float = 0
    crash_bottom_price: float = 0
    crash_pct: float = 0
    crash_duration_hours: float = 0
    volume_surge_ratio: float = 0
    was_predicted: bool = False
    pre_crash_score: int = 0
    pre_crash_phase: str = ""
    pre_crash_signals_json: str = "[]"


class PumpMonitor:
    """爆涨监控器"""

    def __init__(self, store: DataStore):
        self.store = store
        self._init_pump_tables()

    def _init_pump_tables(self):
        """创建爆涨事件专用表"""
        with self.store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pump_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    detected_at INTEGER NOT NULL,
                    pump_start_price REAL,
                    pump_peak_price REAL,
                    pump_pct REAL,
                    pump_duration_hours REAL,
                    volume_surge_ratio REAL,
                    was_predicted INTEGER DEFAULT 0,
                    pre_pump_score INTEGER DEFAULT 0,
                    pre_pump_phase TEXT DEFAULT '',
                    pre_pump_signals_json TEXT DEFAULT '[]',
                    lookback_json TEXT DEFAULT '[]',
                    lesson_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_pump_symbol_ts
                    ON pump_events(symbol, detected_at);

                CREATE TABLE IF NOT EXISTS false_positives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id INTEGER,
                    symbol TEXT NOT NULL,
                    alert_timestamp INTEGER NOT NULL,
                    alert_score INTEGER,
                    alert_phase TEXT,
                    alert_pump_prob INTEGER,
                    alert_signals_json TEXT DEFAULT '[]',
                    price_at_alert REAL,
                    price_after_24h REAL,
                    actual_change_24h REAL,
                    max_change_24h REAL,
                    verified_at INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_fp_symbol
                    ON false_positives(symbol, alert_timestamp);

                CREATE TABLE IF NOT EXISTS learning_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pump_event_id INTEGER,
                    timestamp INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT,
                    old_thresholds_json TEXT,
                    new_thresholds_json TEXT,
                    details_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

    # ── 核心: 爆涨检测 ──────────────────────────────────────────────────

    def check_for_pumps(
        self,
        current_data: Dict[str, Dict],
        btc_change_24h: float = 0,
    ) -> List[PumpEvent]:
        """
        检查所有代币是否发生了爆涨

        current_data: {symbol: {price, change_1h, change_4h, change_24h,
                                volume_current, volume_avg, ...}}
        """
        pump_events = []

        # 排除大盘普涨
        if abs(btc_change_24h) > PUMP_DEF.max_btc_change_24h:
            logger.info(f"[PumpMonitor] BTC 24h {btc_change_24h:+.1f}%, 大盘行情, 跳过个别检测")
            return []

        for symbol, data in current_data.items():
            event = self._check_single(symbol, data)
            if event:
                # 回溯: 拉取爆涨前的快照记录
                event = self._enrich_with_lookback(event)
                # 对比: 是否被预测到
                event = self._check_prediction(event)
                pump_events.append(event)
                self._save_pump_event(event)

                status = "✅ 命中" if event.was_predicted else "❌ 漏报"
                logger.warning(
                    f"🚀 [PUMP] {symbol}: +{event.pump_pct:.1f}% | "
                    f"预测状态: {status} | "
                    f"爆涨前评分: {event.pre_pump_score}"
                )

        return pump_events

    def _check_single(self, symbol: str, data: Dict) -> Optional[PumpEvent]:
        """检测单个代币是否爆涨 (标准: 24h ≥30%)"""
        change_24h = data.get("change_24h", 0)
        vol_current = data.get("volume_current", 0)
        vol_avg = data.get("volume_avg", 1)
        price = data.get("price", 0)

        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1

        if change_24h < PUMP_DEF.min_pump_pct_24h:
            return None
        if vol_ratio < PUMP_DEF.min_volume_surge:
            return None

        # 去重: 同一代币 24h 内不重复记录
        recent = self._get_recent_pump(symbol, hours=24)
        if recent:
            return None

        start_price = price / (1 + change_24h / 100)
        return PumpEvent(
            symbol=symbol,
            detected_at=int(time.time() * 1000),
            pump_start_price=start_price,
            pump_peak_price=price,
            pump_pct=change_24h,
            pump_duration_hours=24,
            volume_surge_ratio=vol_ratio,
        )

    # ── 回溯: 收集爆涨前快照 ────────────────────────────────────────────

    def _enrich_with_lookback(self, event: PumpEvent) -> PumpEvent:
        """收集爆涨前 7 天的历史快照, 用于学习"""
        snapshots = self.store.get_snapshots(event.symbol, limit=200)

        # 找到爆涨前 (最近 7 天 = ~672 个15分钟快照, 取最近 50 个)
        pre_pump = []
        for s in snapshots:
            if s.get("timestamp", 0) < event.detected_at:
                pre_pump.append({
                    "timestamp": s.get("timestamp"),
                    "price": s.get("price"),
                    "control_score": s.get("control_score"),
                    "phase": s.get("phase"),
                    "pump_probability": s.get("pump_probability"),
                    "signals_json": s.get("signals_json"),
                    "metrics_json": s.get("metrics_json"),
                    "change_24h": s.get("change_24h"),
                    "volume_24h": s.get("volume_24h"),
                })
                if len(pre_pump) >= 50:
                    break

        event.lookback_snapshots = pre_pump

        # 取爆涨前最近一次快照作为 "爆涨前状态"
        if pre_pump:
            latest = pre_pump[0]
            event.pre_pump_score = latest.get("control_score", 0)
            event.pre_pump_phase = latest.get("phase", "")
            try:
                event.pre_pump_signals = json.loads(latest.get("signals_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                event.pre_pump_signals = []

        return event

    # ── 对比: 是否被预测到 ────────────────────────────────────────────────

    def _check_prediction(self, event: PumpEvent) -> PumpEvent:
        """检查爆涨前 48h 内是否发出过预警"""
        cutoff = event.detected_at - 48 * 3600 * 1000  # 48 小时前

        with self.store._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM alerts
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
            """, (event.symbol, cutoff, event.detected_at)).fetchone()

            event.was_predicted = (row["cnt"] > 0) if row else False

        return event

    # ── 持久化 ───────────────────────────────────────────────────────────

    def _save_pump_event(self, event: PumpEvent) -> int:
        with self.store._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO pump_events
                (symbol, detected_at, pump_start_price, pump_peak_price, pump_pct,
                 pump_duration_hours, volume_surge_ratio, was_predicted,
                 pre_pump_score, pre_pump_phase, pre_pump_signals_json, lookback_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.symbol, event.detected_at,
                event.pump_start_price, event.pump_peak_price, event.pump_pct,
                event.pump_duration_hours, event.volume_surge_ratio,
                1 if event.was_predicted else 0,
                event.pre_pump_score, event.pre_pump_phase,
                json.dumps(event.pre_pump_signals, ensure_ascii=False),
                json.dumps(event.lookback_snapshots[:20], ensure_ascii=False),
            ))
            return cursor.lastrowid

    def _get_recent_pump(self, symbol: str, hours: int = 24) -> Optional[Dict]:
        cutoff = int((time.time() - hours * 3600) * 1000)
        with self.store._conn() as conn:
            row = conn.execute("""
                SELECT * FROM pump_events
                WHERE symbol = ? AND detected_at >= ?
                ORDER BY detected_at DESC LIMIT 1
            """, (symbol, cutoff)).fetchone()
            return dict(row) if row else None

    # ── 统计 ─────────────────────────────────────────────────────────────

    def get_stats(self, days: int = 30) -> Dict:
        """获取预测准确率统计"""
        cutoff = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM pump_events WHERE detected_at >= ?
            """, (cutoff,)).fetchall()

        total = len(rows)
        if total == 0:
            return {"total_pumps": 0, "predicted": 0, "missed": 0,
                    "hit_rate": 0, "miss_rate": 0, "avg_pre_score": 0}

        predicted = sum(1 for r in rows if r["was_predicted"])
        missed = total - predicted
        avg_pre_score = sum(r["pre_pump_score"] for r in rows) / total
        avg_missed_score = (
            sum(r["pre_pump_score"] for r in rows if not r["was_predicted"]) / missed
            if missed > 0 else 0
        )

        return {
            "total_pumps": total,
            "predicted": predicted,
            "missed": missed,
            "hit_rate": predicted / total * 100,
            "miss_rate": missed / total * 100,
            "avg_pre_score": avg_pre_score,
            "avg_missed_score": avg_missed_score,
            "days": days,
        }

    def get_missed_events(self, days: int = 30) -> List[Dict]:
        """获取所有漏报事件 (用于学习)"""
        cutoff = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM pump_events
                WHERE detected_at >= ? AND was_predicted = 0
                ORDER BY detected_at DESC
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════
    # 误报检测: 预测了暴涨但 24h 内未发生
    # ═══════════════════════════════════════════════════════════════════════

    def verify_past_alerts(self, current_prices: Dict[str, Dict]) -> List[Dict]:
        """
        检查过去发出的预警: 24h 后是否真的暴涨 ≥50%?
        如果没有 → 记录为误报 (false positive)

        current_prices: {symbol: {"price": float, ...}}
        返回: 新发现的误报列表
        """
        # 找出 24-72h 前的预警 (已过验证窗口但尚未验证的)
        now_ms = int(time.time() * 1000)
        window_start = now_ms - 72 * 3600 * 1000  # 72h 前
        window_end = now_ms - 24 * 3600 * 1000    # 24h 前

        with self.store._conn() as conn:
            alerts = conn.execute("""
                SELECT * FROM alerts
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (window_start, window_end)).fetchall()

        if not alerts:
            return []

        false_positives = []

        for alert in alerts:
            alert = dict(alert)  # sqlite3.Row → dict
            symbol = alert["symbol"]
            alert_ts = alert["timestamp"]

            # 检查是否已验证过
            with self.store._conn() as conn:
                existing = conn.execute("""
                    SELECT id FROM false_positives
                    WHERE symbol = ? AND alert_timestamp = ?
                """, (symbol, alert_ts)).fetchone()
            if existing:
                continue

            # 检查这段时间内是否真的发生了爆涨
            with self.store._conn() as conn:
                pump = conn.execute("""
                    SELECT id FROM pump_events
                    WHERE symbol = ? AND detected_at >= ? AND detected_at <= ?
                """, (symbol, alert_ts, alert_ts + 24 * 3600 * 1000)).fetchone()

            if pump:
                # 真的暴涨了 (≥30%) → 命中, 跳过
                continue

            # 额外检查: 24h内最高涨幅是否达到30%
            max_change = self._get_max_change_since(symbol, alert_ts)
            if max_change >= PUMP_DEF.min_pump_pct_24h:
                # 虽然没被 pump_events 记录, 但确实涨了 ≥30% → 不算误报
                continue

            # 没有涨到30% → 误报!
            # 从快照获取预警时和 24h 后的价格
            price_at_alert = alert.get("price") or self._get_price_at(symbol, alert_ts)
            price_now = current_prices.get(symbol, {}).get("price", 0)

            actual_change = 0
            if price_at_alert and price_at_alert > 0 and price_now > 0:
                actual_change = (price_now - price_at_alert) / price_at_alert * 100

            try:
                signals = alert["signals_json"]
            except (KeyError, TypeError):
                signals = "[]"

            fp_record = {
                "alert_id": alert["id"],
                "symbol": symbol,
                "alert_timestamp": alert_ts,
                "alert_score": alert.get("control_score", 0),
                "alert_phase": alert.get("phase", ""),
                "alert_pump_prob": alert.get("pump_probability", 0),
                "alert_signals_json": signals,
                "price_at_alert": price_at_alert or 0,
                "price_after_24h": price_now,
                "actual_change_24h": actual_change,
                "max_change_24h": max_change,
            }

            self._save_false_positive(fp_record)
            false_positives.append(fp_record)

            logger.warning(
                f"🔕 [FALSE POSITIVE] {symbol}: "
                f"预测评分{alert.get('control_score', 0)}, "
                f"实际24h变动{actual_change:+.1f}%, "
                f"最高{max_change:+.1f}%"
            )

        return false_positives

    def _get_price_at(self, symbol: str, timestamp: int) -> Optional[float]:
        """从快照获取某时间点附近的价格"""
        with self.store._conn() as conn:
            row = conn.execute("""
                SELECT price FROM snapshots
                WHERE symbol = ? AND timestamp <= ?
                ORDER BY timestamp DESC LIMIT 1
            """, (symbol, timestamp)).fetchone()
            return row["price"] if row else None

    def _get_max_change_since(self, symbol: str, since_ts: int) -> float:
        """获取自某时间点以来的最大涨幅"""
        base_price = self._get_price_at(symbol, since_ts)
        if not base_price or base_price <= 0:
            return 0

        end_ts = since_ts + 24 * 3600 * 1000
        with self.store._conn() as conn:
            row = conn.execute("""
                SELECT MAX(price) as max_price FROM snapshots
                WHERE symbol = ? AND timestamp > ? AND timestamp <= ?
            """, (symbol, since_ts, end_ts)).fetchone()

        if row and row["max_price"]:
            return (row["max_price"] - base_price) / base_price * 100
        return 0

    def _save_false_positive(self, fp: Dict):
        with self.store._conn() as conn:
            conn.execute("""
                INSERT INTO false_positives
                (alert_id, symbol, alert_timestamp, alert_score, alert_phase,
                 alert_pump_prob, alert_signals_json,
                 price_at_alert, price_after_24h, actual_change_24h, max_change_24h,
                 verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fp.get("alert_id"), fp["symbol"], fp["alert_timestamp"],
                fp.get("alert_score", 0), fp.get("alert_phase", ""),
                fp.get("alert_pump_prob", 0), fp.get("alert_signals_json", "[]"),
                fp.get("price_at_alert", 0), fp.get("price_after_24h", 0),
                fp.get("actual_change_24h", 0), fp.get("max_change_24h", 0),
                int(time.time() * 1000),
            ))

    def get_false_positives(self, days: int = 30) -> List[Dict]:
        cutoff = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM false_positives
                WHERE alert_timestamp >= ?
                ORDER BY alert_timestamp DESC
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    def get_fp_dimension_analysis(self, days: int = 30) -> Dict[str, float]:
        """Which signal dimensions appear more in false positives vs true positives"""
        fps = self.get_false_positives(days)
        fp_dims = Counter()
        for fp in fps:
            try:
                signals = json.loads(fp.get("alert_signals_json", "[]"))
                for sig in signals:
                    dim = sig.get("dimension", "")
                    if dim:
                        fp_dims[dim] += 1
            except (json.JSONDecodeError, TypeError):
                pass

        tp_dims = Counter()
        cutoff = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            pumps = conn.execute(
                "SELECT pre_pump_signals_json FROM pump_events WHERE detected_at >= ? AND was_predicted = 1",
                (cutoff,)).fetchall()
        for p in pumps:
            try:
                signals = json.loads(p["pre_pump_signals_json"])
                for sig in signals:
                    dim = sig.get("dimension", "")
                    if dim:
                        tp_dims[dim] += 1
            except (json.JSONDecodeError, TypeError):
                pass

        all_dims = set(list(fp_dims.keys()) + list(tp_dims.keys()))
        result = {}
        for dim in all_dims:
            fp_c = fp_dims.get(dim, 0)
            tp_c = tp_dims.get(dim, 0)
            total = fp_c + tp_c
            if total > 0:
                result[dim] = fp_c / total
        return result

    def get_full_stats(self, days: int = 30) -> Dict:
        """完整统计: 命中 + 漏报 + 误报"""
        base = self.get_stats(days)
        fps = self.get_false_positives(days)

        # 统计预警总数
        cutoff = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()
        total_alerts = row["cnt"] if row else 0

        fp_count = len(fps)
        true_positive = base["predicted"]
        precision = (
            true_positive / (true_positive + fp_count) * 100
            if (true_positive + fp_count) > 0 else 0
        )
        avg_fp_score = (
            sum(f.get("alert_score", 0) for f in fps) / fp_count
            if fp_count > 0 else 0
        )
        avg_fp_actual_change = (
            sum(f.get("actual_change_24h", 0) for f in fps) / fp_count
            if fp_count > 0 else 0
        )

        return {
            **base,
            "total_alerts": total_alerts,
            "false_positives": fp_count,
            "precision": precision,          # 精确率: TP / (TP + FP)
            "recall": base["hit_rate"],      # 召回率: TP / (TP + FN)
            "avg_fp_score": avg_fp_score,
            "avg_fp_actual_change": avg_fp_actual_change,
        }

    # ══════════════════════════════════════════════════════════════════════
    # 暴跌检测 (Crash Detection)
    # ══════════════════════════════════════════════════════════════════════

    def _init_crash_tables(self):
        with self.store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS crash_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    detected_at INTEGER NOT NULL,
                    crash_start_price REAL,
                    crash_bottom_price REAL,
                    crash_pct REAL,
                    crash_duration_hours REAL,
                    volume_surge_ratio REAL,
                    was_predicted INTEGER DEFAULT 0,
                    pre_crash_score INTEGER DEFAULT 0,
                    pre_crash_phase TEXT DEFAULT '',
                    pre_crash_signals_json TEXT DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS crash_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    crash_score INTEGER,
                    phase TEXT,
                    crash_probability INTEGER,
                    signals_json TEXT,
                    message TEXT
                );
            """)

    def check_for_crashes(
        self, current_data: Dict[str, Dict], btc_change_24h: float = 0
    ) -> List[CrashEvent]:
        """
        检查所有代币是否发生了暴跌

        标准: 4h 跌 ≥50% 或 24h 跌 ≥60%
        """
        # 初始化表 (首次调用)
        self._init_crash_tables()
        crash_events = []

        # 排除大盘崩盘
        if btc_change_24h < CRASH_DEF.max_btc_change_24h:
            logger.info(f"[CrashMonitor] BTC 24h {btc_change_24h:+.1f}%, 大盘暴跌, 跳过")
            return []

        for symbol, data in current_data.items():
            event = self._check_crash_single(symbol, data)
            if event:
                event = self._enrich_crash_lookback(event)
                event = self._check_crash_prediction(event)
                crash_events.append(event)
                self._save_crash_event(event)

                status = "✅ 命中" if event.was_predicted else "❌ 漏报"
                logger.warning(
                    f"📉 [CRASH] {symbol}: {event.crash_pct:+.1f}% | "
                    f"预测状态: {status} | 暴跌前评分: {event.pre_crash_score}"
                )

        return crash_events

    def _check_crash_single(self, symbol: str, data: Dict) -> Optional[CrashEvent]:
        """检测单个代币是否暴跌"""
        change_4h = data.get("change_4h", 0)
        change_24h = data.get("change_24h", 0)
        vol_current = data.get("volume_current", 0)
        vol_avg = data.get("volume_avg", 1)
        price = data.get("price", 0)

        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1

        is_crash = False
        crash_pct = 0
        duration = 0

        # 快速崩盘: 4h 跌 ≥50%
        if change_4h <= -CRASH_DEF.min_crash_pct_4h and vol_ratio >= CRASH_DEF.min_volume_surge:
            is_crash = True
            crash_pct = change_4h
            duration = 4
        # 慢速崩盘: 24h 跌 ≥60%
        elif change_24h <= -CRASH_DEF.min_crash_pct_24h and vol_ratio >= CRASH_DEF.min_volume_surge * 0.8:
            is_crash = True
            crash_pct = change_24h
            duration = 24

        if not is_crash:
            return None

        # 去重
        with self.store._conn() as conn:
            try:
                recent = conn.execute("""
                    SELECT id FROM crash_events
                    WHERE symbol = ? AND detected_at > ?
                """, (symbol, int((time.time() - 24*3600) * 1000))).fetchone()
                if recent:
                    return None
            except Exception:
                pass

        start_price = price / (1 + crash_pct / 100) if crash_pct != -100 else price * 2
        return CrashEvent(
            symbol=symbol,
            detected_at=int(time.time() * 1000),
            crash_start_price=start_price,
            crash_bottom_price=price,
            crash_pct=crash_pct,
            crash_duration_hours=duration,
            volume_surge_ratio=vol_ratio,
        )

    def _enrich_crash_lookback(self, event: CrashEvent) -> CrashEvent:
        """收集暴跌前的快照 (用于学习出货信号)"""
        snapshots = self.store.get_snapshots(event.symbol, limit=100)
        pre_crash = []
        for s in snapshots:
            if s.get("timestamp", 0) < event.detected_at:
                pre_crash.append(s)
            if len(pre_crash) >= 20:
                break

        if pre_crash:
            latest = pre_crash[0]
            event.pre_crash_score = latest.get("control_score", 0)
            event.pre_crash_phase = latest.get("phase", "")
            sigs = latest.get("signals", "[]")
            event.pre_crash_signals_json = json.dumps(sigs) if not isinstance(sigs, str) else sigs

        return event

    def _check_crash_prediction(self, event: CrashEvent) -> CrashEvent:
        """检查暴跌是否被预测过"""
        with self.store._conn() as conn:
            try:
                row = conn.execute("""
                    SELECT id FROM crash_alerts
                    WHERE symbol = ?
                    AND timestamp >= ? AND timestamp <= ?
                """, (event.symbol,
                      event.detected_at - 24 * 3600 * 1000,
                      event.detected_at)).fetchone()
                event.was_predicted = row is not None
            except Exception:
                pass
        return event

    def _save_crash_event(self, event: CrashEvent):
        with self.store._conn() as conn:
            conn.execute("""
                INSERT INTO crash_events
                (symbol, detected_at, crash_start_price, crash_bottom_price,
                 crash_pct, crash_duration_hours, volume_surge_ratio,
                 was_predicted, pre_crash_score, pre_crash_phase, pre_crash_signals_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event.symbol, event.detected_at, event.crash_start_price,
                  event.crash_bottom_price, event.crash_pct, event.crash_duration_hours,
                  event.volume_surge_ratio, int(event.was_predicted),
                  event.pre_crash_score, event.pre_crash_phase, event.pre_crash_signals_json))

    def save_crash_alert(self, symbol: str, crash_score: int, phase: str,
                         crash_probability: int, signals_json: str, message: str):
        """保存暴跌预警 (供 alert_engine 调用)"""
        self._init_crash_tables()
        with self.store._conn() as conn:
            conn.execute("""
                INSERT INTO crash_alerts
                (symbol, timestamp, crash_score, phase, crash_probability, signals_json, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (symbol, int(time.time() * 1000), crash_score, phase,
                  crash_probability, signals_json, message))

    def get_crash_stats(self, days: int = 30) -> Dict:
        """暴跌统计"""
        self._init_crash_tables()
        cutoff = int((time.time() - days * 86400) * 1000)
        with self.store._conn() as conn:
            events = conn.execute(
                "SELECT * FROM crash_events WHERE detected_at >= ?", (cutoff,)
            ).fetchall()
        total = len(events)
        predicted = sum(1 for e in events if e["was_predicted"])
        missed = total - predicted
        return {
            "total_crashes": total,
            "predicted": predicted,
            "missed": missed,
            "hit_rate": predicted / total * 100 if total > 0 else 0,
        }

