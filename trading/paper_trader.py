"""
Paper Trading Engine
Simulates trading with risk management and performance tracking
"""
import sqlite3
import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class TradeConfig:
    """Paper trading configuration"""
    risk_per_trade_pct: float = 2.0
    default_stop_loss_pct: float = 8.0
    default_take_profit_pct: float = 15.0
    max_hold_hours: float = 48.0
    max_open_positions: int = 5
    min_alert_score: int = 75


@dataclass
class TradeData:
    """Trade data model"""
    id: Optional[int]
    symbol: str
    direction: str  # "long" or "short"
    entry_price: float
    entry_time: int
    exit_price: Optional[float]
    exit_time: Optional[int]
    status: str  # "open" or "closed"
    pnl_pct: float
    pnl_usd: float
    alert_score: int
    alert_phase: str
    alert_probability: int
    alert_signals_json: str
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_hours: float
    peak_price: float
    max_drawdown_pct: float
    position_size_usd: float
    close_reason: Optional[str]

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        data = asdict(self)
        if self.alert_signals_json:
            try:
                data['alert_signals'] = json.loads(self.alert_signals_json)
            except json.JSONDecodeError:
                data['alert_signals'] = []
        else:
            data['alert_signals'] = []
        return data


class PaperTrader:
    """Paper trading engine with risk management"""

    def __init__(self, store, initial_capital: float = 10000):
        """
        Initialize paper trader

        Args:
            store: DataStore instance with _conn() context manager
            initial_capital: Starting capital in USD
        """
        self.store = store
        self.initial_capital = initial_capital
        self.config = TradeConfig()
        self._init_tables()

    def _init_tables(self):
        """Initialize database tables for paper trading"""
        with self.store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time INTEGER NOT NULL,
                    exit_price REAL,
                    exit_time INTEGER,
                    status TEXT NOT NULL,
                    pnl_pct REAL DEFAULT 0,
                    pnl_usd REAL DEFAULT 0,
                    alert_score INTEGER,
                    alert_phase TEXT,
                    alert_probability INTEGER,
                    alert_signals_json TEXT,
                    stop_loss_pct REAL NOT NULL,
                    take_profit_pct REAL NOT NULL,
                    max_hold_hours REAL NOT NULL,
                    peak_price REAL DEFAULT 0,
                    max_drawdown_pct REAL DEFAULT 0,
                    position_size_usd REAL NOT NULL,
                    close_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol
                    ON paper_trades(symbol);
                CREATE INDEX IF NOT EXISTS idx_paper_trades_status
                    ON paper_trades(status);
                CREATE INDEX IF NOT EXISTS idx_paper_trades_entry_time
                    ON paper_trades(entry_time);

                CREATE TABLE IF NOT EXISTS paper_trade_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    avg_pnl_pct REAL DEFAULT 0,
                    avg_win_pct REAL DEFAULT 0,
                    avg_loss_pct REAL DEFAULT 0,
                    total_pnl_usd REAL DEFAULT 0,
                    sharpe_ratio REAL DEFAULT 0,
                    best_trade_pct REAL DEFAULT 0,
                    worst_trade_pct REAL DEFAULT 0,
                    avg_hold_hours REAL DEFAULT 0,
                    long_trades INTEGER DEFAULT 0,
                    short_trades INTEGER DEFAULT 0,
                    long_win_rate REAL DEFAULT 0,
                    short_win_rate REAL DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date)
                );

                CREATE INDEX IF NOT EXISTS idx_paper_trade_stats_date
                    ON paper_trade_stats(date);
            """)
        logger.info("[PaperTrader] Database tables initialized")

    def open_position(
        self,
        symbol: str,
        direction: str,
        price: float,
        alert_data: Optional[Dict] = None,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None
    ) -> Optional[TradeData]:
        """
        Open a new trading position

        Args:
            symbol: Trading symbol
            direction: "long" or "short"
            price: Entry price
            alert_data: Alert data dict with score, phase, probability, signals
            sl_pct: Custom stop loss percentage
            tp_pct: Custom take profit percentage

        Returns:
            TradeData if position opened, None if rejected
        """
        if direction not in ("long", "short"):
            logger.error(f"[PaperTrader] Invalid direction: {direction}")
            return None

        alert_data = alert_data or {}
        alert_score = alert_data.get("control_score", alert_data.get("alert_score", 0))

        # Check minimum alert score
        if alert_score < self.config.min_alert_score:
            logger.info(f"[PaperTrader] Alert score {alert_score} below minimum {self.config.min_alert_score}")
            return None

        with self.store._conn() as conn:
            # Check max open positions
            open_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM paper_trades WHERE status = 'open'"
            ).fetchone()["cnt"]
            if open_count >= self.config.max_open_positions:
                logger.info(f"[PaperTrader] Max open positions ({self.config.max_open_positions}) reached")
                return None

            # Check for duplicate position (same symbol + direction)
            duplicate = conn.execute(
                "SELECT id FROM paper_trades WHERE symbol = ? AND direction = ? AND status = 'open'",
                (symbol.upper(), direction)
            ).fetchone()
            if duplicate:
                logger.info(f"[PaperTrader] Duplicate position: {symbol} {direction}")
                return None

            # Calculate position size
            sl_pct = sl_pct if sl_pct is not None else self.config.default_stop_loss_pct
            tp_pct = tp_pct if tp_pct is not None else self.config.default_take_profit_pct

            risk_usd = self.initial_capital * (self.config.risk_per_trade_pct / 100)
            position_size_usd = risk_usd / (sl_pct / 100)

            # Prepare alert data
            alert_phase = alert_data.get("phase", "unknown")
            alert_probability = alert_data.get("pump_probability", 0)
            alert_signals = alert_data.get("signals", [])

            # Insert trade
            cursor = conn.execute("""
                INSERT INTO paper_trades
                (symbol, direction, entry_price, entry_time, status,
                 pnl_pct, pnl_usd, alert_score, alert_phase, alert_probability,
                 alert_signals_json, stop_loss_pct, take_profit_pct, max_hold_hours,
                 peak_price, max_drawdown_pct, position_size_usd)
                VALUES (?, ?, ?, ?, 'open', 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol.upper(),
                direction,
                price,
                int(datetime.now().timestamp() * 1000),
                alert_score,
                alert_phase,
                alert_probability,
                json.dumps(alert_signals, ensure_ascii=False),
                sl_pct,
                tp_pct,
                self.config.max_hold_hours,
                price,
                0,
                position_size_usd
            ))

            trade_id = cursor.lastrowid
            logger.info(f"[PaperTrader] Opened {direction} position #{trade_id}: {symbol} @ ${price:.4f}")

        return self._get_trade_by_id(trade_id)

    def check_positions(self, current_prices: Dict[str, float]) -> List[TradeData]:
        """
        Check all open positions against current prices

        Args:
            current_prices: Dict of symbol -> current price

        Returns:
            List of closed TradeData objects
        """
        closed_trades = []
        now_ms = int(datetime.now().timestamp() * 1000)

        with self.store._conn() as conn:
            open_trades = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'open'"
            ).fetchall()

            for row in open_trades:
                trade = self._row_to_trade(row)
                symbol = trade.symbol

                if symbol not in current_prices:
                    continue

                current_price = current_prices[symbol]
                close_reason = None

                # Calculate price change
                if trade.direction == "long":
                    price_change_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
                else:  # short
                    price_change_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100

                # Update peak price and max drawdown
                new_peak = max(trade.peak_price, current_price)
                if trade.direction == "long":
                    drawdown = ((new_peak - current_price) / new_peak) * 100 if new_peak > 0 else 0
                else:
                    drawdown = ((current_price - new_peak) / new_peak) * 100 if new_peak > 0 else 0

                max_drawdown = max(trade.max_drawdown_pct, drawdown)

                conn.execute("""
                    UPDATE paper_trades
                    SET peak_price = ?, max_drawdown_pct = ?
                    WHERE id = ?
                """, (new_peak, max_drawdown, trade.id))

                # Check exit conditions
                if price_change_pct >= trade.take_profit_pct:
                    close_reason = "take_profit"
                elif price_change_pct <= -trade.stop_loss_pct:
                    close_reason = "stop_loss"
                else:
                    # Check timeout
                    entry_time_sec = trade.entry_time / 1000
                    now_sec = now_ms / 1000
                    hold_hours = (now_sec - entry_time_sec) / 3600
                    if hold_hours >= trade.max_hold_hours:
                        close_reason = "timeout"

                if close_reason:
                    closed_trade = self._close_trade(trade.id, current_price, now_ms, close_reason, conn)
                    if closed_trade:
                        closed_trades.append(closed_trade)

        return closed_trades

    def _close_trade(
        self,
        trade_id: int,
        exit_price: float,
        exit_time: int,
        reason: str,
        conn
    ) -> Optional[TradeData]:
        """Close a trade and calculate PnL"""
        row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return None

        trade = self._row_to_trade(row)

        # Calculate PnL
        if trade.direction == "long":
            pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
        else:  # short
            pnl_pct = ((trade.entry_price - exit_price) / trade.entry_price) * 100

        pnl_usd = trade.position_size_usd * (pnl_pct / 100)

        # Update trade
        conn.execute("""
            UPDATE paper_trades
            SET exit_price = ?, exit_time = ?, status = 'closed',
                pnl_pct = ?, pnl_usd = ?, close_reason = ?
            WHERE id = ?
        """, (exit_price, exit_time, pnl_pct, pnl_usd, reason, trade_id))

        logger.info(
            f"[PaperTrader] Closed {trade.direction} #{trade_id}: {trade.symbol} "
            f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f}) [{reason}]"
        )

        # Refresh and return
        row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        return self._row_to_trade(row)

    def get_stats(self, days: int = 30) -> Dict:
        """
        Calculate trading statistics

        Args:
            days: Number of days to analyze

        Returns:
            Dict with statistics
        """
        cutoff_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        with self.store._conn() as conn:
            # Get closed trades in period
            rows = conn.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'closed' AND exit_time >= ?
                ORDER BY exit_time DESC
            """, (cutoff_time,)).fetchall()

            trades = [self._row_to_trade(row) for row in rows]

            if not trades:
                return {
                    "period_days": days,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0,
                    "avg_pnl_pct": 0,
                    "avg_win_pct": 0,
                    "avg_loss_pct": 0,
                    "total_pnl_usd": 0,
                    "sharpe_ratio": 0,
                    "best_trade": None,
                    "worst_trade": None,
                    "avg_hold_hours": 0,
                    "by_direction": {
                        "long": {"trades": 0, "wins": 0, "win_rate": 0},
                        "short": {"trades": 0, "wins": 0, "win_rate": 0}
                    }
                }

            # Basic stats
            total_trades = len(trades)
            winning_trades = sum(1 for t in trades if t.pnl_pct > 0)
            losing_trades = sum(1 for t in trades if t.pnl_pct < 0)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

            pnls = [t.pnl_pct for t in trades]
            avg_pnl_pct = sum(pnls) / len(pnls)

            wins = [t.pnl_pct for t in trades if t.pnl_pct > 0]
            losses = [t.pnl_pct for t in trades if t.pnl_pct < 0]
            avg_win_pct = sum(wins) / len(wins) if wins else 0
            avg_loss_pct = sum(losses) / len(losses) if losses else 0

            total_pnl_usd = sum(t.pnl_usd for t in trades)

            # Sharpe ratio (simplified)
            if len(pnls) > 1:
                import statistics
                mean = statistics.mean(pnls)
                stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
                sharpe_ratio = (mean / stdev * (252 ** 0.5)) if stdev > 0 else 0
            else:
                sharpe_ratio = 0

            best_trade = max(trades, key=lambda t: t.pnl_pct)
            worst_trade = min(trades, key=lambda t: t.pnl_pct)

            # Average hold time
            hold_times = []
            for t in trades:
                if t.exit_time and t.entry_time:
                    hours = (t.exit_time - t.entry_time) / 1000 / 3600
                    hold_times.append(hours)
            avg_hold_hours = sum(hold_times) / len(hold_times) if hold_times else 0

            # By direction
            long_trades = [t for t in trades if t.direction == "long"]
            short_trades = [t for t in trades if t.direction == "short"]

            long_wins = sum(1 for t in long_trades if t.pnl_pct > 0)
            short_wins = sum(1 for t in short_trades if t.pnl_pct > 0)

            by_direction = {
                "long": {
                    "trades": len(long_trades),
                    "wins": long_wins,
                    "win_rate": (long_wins / len(long_trades) * 100) if long_trades else 0
                },
                "short": {
                    "trades": len(short_trades),
                    "wins": short_wins,
                    "win_rate": (short_wins / len(short_trades) * 100) if short_trades else 0
                }
            }

        return {
            "period_days": days,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "avg_pnl_pct": round(avg_pnl_pct, 2),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "best_trade": {
                "symbol": best_trade.symbol,
                "direction": best_trade.direction,
                "pnl_pct": round(best_trade.pnl_pct, 2),
                "pnl_usd": round(best_trade.pnl_usd, 2)
            },
            "worst_trade": {
                "symbol": worst_trade.symbol,
                "direction": worst_trade.direction,
                "pnl_pct": round(worst_trade.pnl_pct, 2),
                "pnl_usd": round(worst_trade.pnl_usd, 2)
            },
            "avg_hold_hours": round(avg_hold_hours, 2),
            "by_direction": {
                "long": {
                    "trades": by_direction["long"]["trades"],
                    "wins": by_direction["long"]["wins"],
                    "win_rate": round(by_direction["long"]["win_rate"], 2)
                },
                "short": {
                    "trades": by_direction["short"]["trades"],
                    "wins": by_direction["short"]["wins"],
                    "win_rate": round(by_direction["short"]["win_rate"], 2)
                }
            }
        }

    def get_open_positions(self) -> List[Dict]:
        """Get all open positions"""
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'open'
                ORDER BY entry_time DESC
            """).fetchall()
            return [dict(row) for row in rows]

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent closed trades"""
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'closed'
                ORDER BY exit_time DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def _row_to_trade(self, row) -> TradeData:
        """Convert database row to TradeData, filtering extra columns"""
        data = dict(row)
        # Remove columns not in TradeData dataclass
        data.pop('created_at', None)
        return TradeData(**data)

    def _get_trade_by_id(self, trade_id: int) -> Optional[TradeData]:
        """Get trade by ID"""
        with self.store._conn() as conn:
            row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
            if row:
                return self._row_to_trade(row)
        return None

    def format_stats_message(self, stats: Dict) -> str:
        """
        Format statistics as Telegram message

        Args:
            stats: Stats dict from get_stats()

        Returns:
            Formatted message string
        """
        lines = [
            "📊 *Paper Trading Stats*",
            f"",
            f"📅 Period: {stats['period_days']} days",
            f"",
            f"📈 *Performance*",
            f"  Trades: {stats['total_trades']} ({stats['winning_trades']}W {stats['losing_trades']}L)",
            f"  Win Rate: {stats['win_rate']}%",
            f"  Avg PnL: {stats['avg_pnl_pct']:+.2f}%",
            f"  Total PnL: ${stats['total_pnl_usd']:+.2f}",
            f"",
            f"🎯 *Best & Worst*",
        ]

        if stats['best_trade']:
            bt = stats['best_trade']
            lines.append(f"  ✅ Best: {bt['symbol']} {bt['direction']} {bt['pnl_pct']:+.2f}% (${bt['pnl_usd']:+.2f})")

        if stats['worst_trade']:
            wt = stats['worst_trade']
            lines.append(f"  ❌ Worst: {wt['symbol']} {wt['direction']} {wt['pnl_pct']:+.2f}% (${wt['pnl_usd']:+.2f})")

        lines.extend([
            f"",
            f"📊 *Avg Win/Loss*",
            f"  Avg Win: {stats['avg_win_pct']:+.2f}%",
            f"  Avg Loss: {stats['avg_loss_pct']:+.2f}%",
            f"",
            f"⏱️ Avg Hold: {stats['avg_hold_hours']:.1f}h",
            f"",
            f"📊 *By Direction*",
        ])

        for direction in ['long', 'short']:
            d = stats['by_direction'][direction]
            if d['trades'] > 0:
                lines.append(f"  {direction.title()}: {d['trades']} trades, {d['win_rate']}% win rate")

        lines.extend([
            f"",
            f"📈 Sharpe Ratio: {stats['sharpe_ratio']:.2f}"
        ])

        return "\n".join(lines)
