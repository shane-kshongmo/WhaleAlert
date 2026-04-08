"""
SQLite 数据存储
- 代币快照历史
- 控盘评分历史
- 预警记录
"""
import sqlite3
import json
import time
import logging
from typing import Dict, List, Optional
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)


class DataStore:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        self._init_db()

    @contextmanager
    def _conn(self):
        if self.db_path == ":memory:":
            # In-memory DB: reuse single connection
            if not hasattr(self, "_mem_conn") or self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:")
                self._mem_conn.row_factory = sqlite3.Row
            yield self._mem_conn
            self._mem_conn.commit()
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    price REAL,
                    volume_24h REAL,
                    change_24h REAL,
                    control_score INTEGER,
                    phase TEXT,
                    pump_probability INTEGER,
                    signals_json TEXT,
                    metrics_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_ts
                    ON snapshots(symbol, timestamp);

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    control_score INTEGER,
                    phase TEXT,
                    pump_probability INTEGER,
                    signals_json TEXT,
                    message TEXT,
                    sent_telegram INTEGER DEFAULT 0,
                    sent_webhook INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_alerts_symbol_ts
                    ON alerts(symbol, timestamp);

                CREATE TABLE IF NOT EXISTS kline_cache (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, quote_volume REAL,
                    trades INTEGER,
                    taker_buy_volume REAL,
                    PRIMARY KEY (symbol, interval, timestamp)
                );
            """)
        logger.info(f"[DB] 初始化完成: {self.db_path}")

    # ── 快照 ─────────────────────────────────────────────────────────────

    def save_snapshot(self, data: Dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO snapshots
                (symbol, timestamp, price, volume_24h, change_24h,
                 control_score, phase, pump_probability, signals_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["symbol"],
                data.get("timestamp", int(time.time() * 1000)),
                data.get("price", 0),
                data.get("volume_24h", 0),
                data.get("change_24h", 0),
                data.get("control_score", 0),
                data.get("phase", ""),
                data.get("pump_probability", 0),
                json.dumps(data.get("signals", []), ensure_ascii=False),
                json.dumps(data.get("metrics", {}), ensure_ascii=False),
            ))

    def get_latest_snapshot(self, symbol: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM snapshots
                WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT 1
            """, (symbol,)).fetchone()
            if row:
                return dict(row)
        return None

    def get_snapshots(self, symbol: str, limit: int = 100) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM snapshots
                WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (symbol, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_all_latest(self) -> List[Dict]:
        """获取所有代币的最新快照"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT s.* FROM snapshots s
                INNER JOIN (
                    SELECT symbol, MAX(timestamp) as max_ts
                    FROM snapshots GROUP BY symbol
                ) latest ON s.symbol = latest.symbol AND s.timestamp = latest.max_ts
                ORDER BY s.control_score DESC
            """).fetchall()
            return [dict(r) for r in rows]

    # ── 预警 ─────────────────────────────────────────────────────────────

    def save_alert(self, data: Dict) -> int:
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO alerts
                (symbol, timestamp, control_score, phase, pump_probability, signals_json, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data["symbol"],
                data.get("timestamp", int(time.time() * 1000)),
                data.get("control_score", 0),
                data.get("phase", ""),
                data.get("pump_probability", 0),
                json.dumps(data.get("signals", []), ensure_ascii=False),
                data.get("message", ""),
            ))
            return cursor.lastrowid

    def mark_alert_sent(self, alert_id: int, channel: str):
        col = f"sent_{channel}"
        with self._conn() as conn:
            conn.execute(f"UPDATE alerts SET {col} = 1 WHERE id = ?", (alert_id,))

    def get_last_alert_time(self, symbol: str) -> Optional[int]:
        """获取某代币上次预警时间"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT MAX(timestamp) as last_ts FROM alerts WHERE symbol = ?
            """, (symbol,)).fetchone()
            if row and row["last_ts"]:
                return row["last_ts"]
        return None

    def get_recent_alerts(self, hours: int = 24) -> List[Dict]:
        cutoff = int((time.time() - hours * 3600) * 1000)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM alerts WHERE timestamp >= ?
                ORDER BY timestamp DESC
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    # ── K线缓存 ──────────────────────────────────────────────────────────

    def cache_klines(self, symbol: str, interval: str, bars: List):
        with self._conn() as conn:
            for bar in bars:
                conn.execute("""
                    INSERT OR REPLACE INTO kline_cache
                    (symbol, interval, timestamp, open, high, low, close,
                     volume, quote_volume, trades, taker_buy_volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, interval, bar.timestamp,
                    bar.open, bar.high, bar.low, bar.close,
                    bar.volume, bar.quote_volume, bar.trades, bar.taker_buy_volume,
                ))

    def get_cached_klines(self, symbol: str, interval: str, limit: int = 2160) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM kline_cache
                WHERE symbol = ? AND interval = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (symbol, interval, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    # ── 清理 ─────────────────────────────────────────────────────────────

    def cleanup(self, keep_days: int = 180):
        """清理旧数据"""
        cutoff = int((time.time() - keep_days * 86400) * 1000)
        with self._conn() as conn:
            conn.execute("DELETE FROM snapshots WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM kline_cache WHERE timestamp < ?", (cutoff,))
            conn.execute("VACUUM")
        logger.info(f"[DB] 清理 {keep_days} 天前的数据")
