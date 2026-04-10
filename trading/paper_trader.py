"""
Paper Trading Engine — Strategy v2
Dynamic SL/TP, trailing stops, score-deterioration exits, tiered position sizing, eviction policy
"""
import sqlite3
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class SignalTier(Enum):
    """Signal confidence tier based on control_score + pump_probability"""
    STRONG = "strong"      # score >= 65
    MEDIUM = "medium"      # score >= 50
    WEAK = "weak"          # score >= 40 (minimum to trade)


@dataclass
class TierConfig:
    """Per-tier trading parameters"""
    sl_pct: float
    tp_pct: float
    trailing_activate_pct: float   # profit % before trailing activates
    trailing_distance_pct: float   # trail distance from peak
    max_hold_hours: float
    position_size_multiplier: float


# Tier configs calibrated for whale manipulation patterns:
# STRONG: tight stops, wide targets (whales push hard when confident)
# MEDIUM: standard stops/targets
# WEAK: wider stops (more noise), modest targets
TIER_CONFIGS = {
    SignalTier.STRONG: TierConfig(
        sl_pct=6.0, tp_pct=18.0,
        trailing_activate_pct=4.0, trailing_distance_pct=2.5,
        max_hold_hours=36.0, position_size_multiplier=2.0,
    ),
    SignalTier.MEDIUM: TierConfig(
        sl_pct=8.0, tp_pct=14.0,
        trailing_activate_pct=5.0, trailing_distance_pct=3.5,
        max_hold_hours=48.0, position_size_multiplier=1.5,
    ),
    SignalTier.WEAK: TierConfig(
        sl_pct=10.0, tp_pct=10.0,
        trailing_activate_pct=6.0, trailing_distance_pct=5.0,
        max_hold_hours=36.0, position_size_multiplier=1.0,
    ),
}


@dataclass
class TradeConfig:
    """Paper trading global configuration"""
    risk_per_trade_pct: float = 2.0
    max_open_positions: int = 8
    min_alert_score: int = 40
    min_volume_24h: float = 200_000  # minimum 24h volume (USDT) — CAKEUSDT lesson: $60K too illiquid
    # Score deterioration exit thresholds
    score_exit_warning: int = 25     # close if score drops below this
    score_exit_force: int = 15       # force close regardless of PnL
    # Eviction: allow evicting weakest position for a stronger signal
    eviction_enabled: bool = True
    eviction_min_score_gap: int = 10  # new signal must be 10+ pts above weakest
    # Gap protection: if price gapped against us while unmonitored
    gap_max_loss_pct: float = 12.0    # force close if unrealized loss exceeds this from any gap


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
    signal_tier: str = "medium"              # strong/medium/weak
    trailing_activated: int = 0              # 0=no, 1=yes
    trailing_stop_price: float = 0.0

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


def classify_tier(control_score: int, pump_probability: int = 0) -> SignalTier:
    """Classify signal strength into a tier"""
    if control_score >= 65:
        return SignalTier.STRONG
    elif control_score >= 50:
        return SignalTier.MEDIUM
    else:
        return SignalTier.WEAK


class PaperTrader:
    """Paper trading engine with dynamic risk management"""

    def __init__(self, store, initial_capital: float = 10000):
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
                    signal_tier TEXT DEFAULT 'medium',
                    trailing_activated INTEGER DEFAULT 0,
                    trailing_stop_price REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol
                    ON paper_trades(symbol);
                CREATE INDEX IF NOT EXISTS idx_paper_trades_status
                    ON paper_trades(status);
                CREATE INDEX IF NOT EXISTS idx_paper_trades_entry_time
                    ON paper_trades(entry_time);
            """)

            # Migration: add new columns if they don't exist
            try:
                conn.execute("SELECT signal_tier FROM paper_trades LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE paper_trades ADD COLUMN signal_tier TEXT DEFAULT 'medium'")
            try:
                conn.execute("SELECT trailing_activated FROM paper_trades LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE paper_trades ADD COLUMN trailing_activated INTEGER DEFAULT 0")
            try:
                conn.execute("SELECT trailing_stop_price FROM paper_trades LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE paper_trades ADD COLUMN trailing_stop_price REAL DEFAULT 0")

        logger.info("[PaperTrader] Database tables initialized")

    def _get_tier_params(self, tier: SignalTier) -> TierConfig:
        """Get trading parameters for a signal tier"""
        return TIER_CONFIGS[tier]

    def _calculate_position_size(self, tier: SignalTier, sl_pct: float) -> float:
        """Dynamic position sizing based on signal tier"""
        tier_cfg = self._get_tier_params(tier)
        base_risk_usd = self.initial_capital * (self.config.risk_per_trade_pct / 100)
        adjusted_risk = base_risk_usd * tier_cfg.position_size_multiplier
        position_size = adjusted_risk / (sl_pct / 100)
        # Cap at 25% of total capital per position
        max_size = self.initial_capital * 0.25
        return min(position_size, max_size)

    def open_position(
        self,
        symbol: str,
        direction: str,
        price: float,
        alert_data: Optional[Dict] = None,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
        volume_24h: float = 0,
    ) -> Optional[TradeData]:
        """Open a new trading position with dynamic sizing and tier-based SL/TP"""
        if direction not in ("long", "short"):
            logger.error(f"[PaperTrader] Invalid direction: {direction}")
            return None

        if price <= 0:
            logger.error(f"[PaperTrader] Invalid price: {price}")
            return None

        alert_data = alert_data or {}
        alert_score = alert_data.get("score", alert_data.get("control_score", alert_data.get("alert_score", 0)))
        alert_probability = alert_data.get("pump_probability", 0)

        # Check minimum alert score
        if alert_score < self.config.min_alert_score:
            logger.info(f"[PaperTrader] Alert score {alert_score} below minimum {self.config.min_alert_score}")
            return None

        # Volume filter: reject illiquid tokens (CAKEUSDT lesson: $60K volume → -15% loss)
        if volume_24h > 0 and volume_24h < self.config.min_volume_24h:
            logger.info(
                f"[PaperTrader] Volume too low: {symbol} ${volume_24h:,.0f} < ${self.config.min_volume_24h:,.0f}")
            return None

        # Determine signal tier
        tier = classify_tier(alert_score, alert_probability)
        tier_cfg = self._get_tier_params(tier)

        # Dynamic SL/TP: use provided values or fall back to tier defaults
        if sl_pct is None or sl_pct <= 0:
            sl_pct = tier_cfg.sl_pct
        if tp_pct is None or tp_pct <= 0:
            tp_pct = tier_cfg.tp_pct

        # Enforce minimum risk:reward of 1.2
        if tp_pct / sl_pct < 1.2:
            tp_pct = sl_pct * 1.5

        with self.store._conn() as conn:
            # Check max open positions
            open_trades = conn.execute(
                "SELECT id, symbol, alert_score, pnl_pct FROM paper_trades WHERE status = 'open'"
            ).fetchall()

            if len(open_trades) >= self.config.max_open_positions:
                # Eviction: check if new signal is strong enough to replace weakest
                if self.config.eviction_enabled:
                    evicted = self._try_evict(open_trades, alert_score, conn)
                    if not evicted:
                        logger.info(f"[PaperTrader] Max positions ({self.config.max_open_positions}) reached, no eviction candidate")
                        return None
                else:
                    logger.info(f"[PaperTrader] Max positions ({self.config.max_open_positions}) reached")
                    return None

            # Check for duplicate position (same symbol + direction)
            duplicate = conn.execute(
                "SELECT id FROM paper_trades WHERE symbol = ? AND direction = ? AND status = 'open'",
                (symbol.upper(), direction)
            ).fetchone()
            if duplicate:
                logger.info(f"[PaperTrader] Duplicate position: {symbol} {direction}")
                return None

            # Dynamic position sizing
            position_size_usd = self._calculate_position_size(tier, sl_pct)

            # Prepare alert data
            alert_phase = alert_data.get("phase", "unknown")
            alert_signals = alert_data.get("signals", [])

            # Insert trade
            cursor = conn.execute("""
                INSERT INTO paper_trades
                (symbol, direction, entry_price, entry_time, status,
                 pnl_pct, pnl_usd, alert_score, alert_phase, alert_probability,
                 alert_signals_json, stop_loss_pct, take_profit_pct, max_hold_hours,
                 peak_price, max_drawdown_pct, position_size_usd,
                 signal_tier, trailing_activated, trailing_stop_price)
                VALUES (?, ?, ?, ?, 'open', 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
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
                tier_cfg.max_hold_hours,
                price,
                0,
                position_size_usd,
                tier.value,
            ))

            trade_id = cursor.lastrowid
            logger.info(
                f"[PaperTrader] Opened {tier.value} {direction} #{trade_id}: "
                f"{symbol} @ ${price:.4f} SL={sl_pct}% TP={tp_pct}% size=${position_size_usd:.0f}"
            )

        return self._get_trade_by_id(trade_id)

    def _try_evict(self, open_trades, new_score: int, conn) -> bool:
        """Try to evict the weakest position for a stronger signal"""
        # Find weakest: lowest score, break ties by worst PnL
        weakest = None
        for t in open_trades:
            score_gap = new_score - t["alert_score"]
            if score_gap >= self.config.eviction_min_score_gap:
                if weakest is None or t["alert_score"] < weakest["alert_score"]:
                    weakest = t

        if weakest is None:
            return False

        # Close the weakest position at "eviction"
        now_ms = int(datetime.now().timestamp() * 1000)
        # We don't have current price here, use entry price (will be updated on next check)
        closed = self._close_trade(weakest["id"], 0, now_ms, "evicted", conn)
        if closed:
            logger.info(
                f"[PaperTrader] Evicted #{weakest['id']} {weakest['symbol']} "
                f"(score={weakest['alert_score']}) for new signal (score={new_score})"
            )
            return True
        return False

    def check_positions(self, current_prices: Dict[str, float],
                        latest_scores: Optional[Dict[str, int]] = None) -> List[TradeData]:
        """
        Check all open positions against current prices.
        Includes: SL/TP, trailing stop, timeout, score deterioration.
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

                # Get current price
                if symbol not in current_prices:
                    continue
                current_price = current_prices[symbol]
                if isinstance(current_price, dict):
                    current_price = current_price.get("price", 0)
                if not current_price or current_price <= 0:
                    continue

                close_reason = None

                # Calculate unrealized PnL
                if trade.direction == "long":
                    price_change_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
                else:
                    price_change_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100

                # ── 1. Hard Stop Loss ──
                if price_change_pct <= -trade.stop_loss_pct:
                    close_reason = "stop_loss"

                # ── 2. Take Profit ──
                elif price_change_pct >= trade.take_profit_pct:
                    close_reason = "take_profit"

                # ── 3. Trailing Stop ──
                if not close_reason:
                    close_reason = self._check_trailing_stop(trade, current_price, price_change_pct, conn)

                # ── 3.5. Gap Protection ──
                # If loss exceeds gap_max_loss_pct, close immediately regardless of SL
                # This catches overnight crashes where price gapped past the stop-loss
                if not close_reason and price_change_pct <= -self.config.gap_max_loss_pct:
                    close_reason = "gap_crash"

                # ── 4. Score Deterioration Exit ──
                if not close_reason and latest_scores:
                    close_reason = self._check_score_exit(trade, latest_scores, price_change_pct)

                # ── 5. Timeout ──
                if not close_reason:
                    entry_time_sec = trade.entry_time / 1000
                    now_sec = now_ms / 1000
                    hold_hours = (now_sec - entry_time_sec) / 3600
                    if hold_hours >= trade.max_hold_hours:
                        close_reason = "timeout"

                # Update peak price and drawdown regardless
                self._update_peak_drawdown(trade, current_price, conn)

                if close_reason:
                    closed_trade = self._close_trade(trade.id, current_price, now_ms, close_reason, conn)
                    if closed_trade:
                        closed_trades.append(closed_trade)

        return closed_trades

    def _check_trailing_stop(self, trade: TradeData, current_price: float,
                              price_change_pct: float, conn) -> Optional[str]:
        """Check and update trailing stop logic"""
        tier = SignalTier(trade.signal_tier) if trade.signal_tier in ("strong", "medium", "weak") else SignalTier.MEDIUM
        tier_cfg = self._get_tier_params(tier)

        # Update peak price
        new_peak = max(trade.peak_price, current_price)

        # Check if trailing should activate
        if not trade.trailing_activated:
            if price_change_pct >= tier_cfg.trailing_activate_pct:
                # Activate trailing stop
                if trade.direction == "long":
                    trail_price = new_peak * (1 - tier_cfg.trailing_distance_pct / 100)
                else:
                    trail_price = new_peak * (1 + tier_cfg.trailing_distance_pct / 100)

                conn.execute("""
                    UPDATE paper_trades
                    SET trailing_activated = 1, trailing_stop_price = ?, peak_price = ?
                    WHERE id = ?
                """, (trail_price, new_peak, trade.id))
                logger.info(
                    f"[PaperTrader] Trailing activated #{trade.id} {trade.symbol} "
                    f"trail=${trail_price:.6f} peak=${new_peak:.6f}"
                )
            return None

        # Trailing is active — update trail price (only moves favorably)
        if trade.direction == "long":
            new_trail = new_peak * (1 - tier_cfg.trailing_distance_pct / 100)
            best_trail = max(trade.trailing_stop_price, new_trail)
        else:
            new_trail = new_peak * (1 + tier_cfg.trailing_distance_pct / 100)
            best_trail = min(trade.trailing_stop_price, new_trail) if trade.trailing_stop_price > 0 else new_trail

        conn.execute("""
            UPDATE paper_trades
            SET trailing_stop_price = ?, peak_price = ?
            WHERE id = ?
        """, (best_trail, new_peak, trade.id))

        # Check if hit trailing stop
        if trade.direction == "long" and current_price <= best_trail:
            return "trailing_stop"
        elif trade.direction == "short" and current_price >= best_trail:
            return "trailing_stop"

        return None

    def _check_score_exit(self, trade: TradeData, latest_scores: Dict[str, int],
                           price_change_pct: float) -> Optional[str]:
        """Check if control score has deteriorated enough to exit"""
        score = latest_scores.get(trade.symbol)
        if score is None:
            return None

        # Force exit if score crashed
        if score < self.config.score_exit_force:
            return "score_crash"

        # Warning exit: score dropped and position is losing
        if score < self.config.score_exit_warning and price_change_pct < 0:
            return "score_deterioration"

        return None

    def _update_peak_drawdown(self, trade: TradeData, current_price: float, conn):
        """Update peak price and max drawdown"""
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

    def _close_trade(self, trade_id: int, exit_price: float, exit_time: int,
                     reason: str, conn) -> Optional[TradeData]:
        """Close a trade and calculate PnL"""
        row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return None

        trade = self._row_to_trade(row)

        # For evicted trades without price, use entry price (neutral close)
        if exit_price <= 0:
            exit_price = trade.entry_price

        if trade.direction == "long":
            pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
        else:
            pnl_pct = ((trade.entry_price - exit_price) / trade.entry_price) * 100

        pnl_usd = trade.position_size_usd * (pnl_pct / 100)

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

        row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        return self._row_to_trade(row)

    def get_stats(self, days: int = 30) -> Dict:
        """Calculate trading statistics"""
        cutoff_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'closed' AND exit_time >= ?
                ORDER BY exit_time DESC
            """, (cutoff_time,)).fetchall()

            trades = [self._row_to_trade(row) for row in rows]

            if not trades:
                return {
                    "period_days": days, "total_trades": 0,
                    "winning_trades": 0, "losing_trades": 0,
                    "win_rate": 0, "avg_pnl_pct": 0,
                    "avg_win_pct": 0, "avg_loss_pct": 0,
                    "total_pnl_usd": 0, "sharpe_ratio": 0,
                    "best_trade": None, "worst_trade": None,
                    "avg_hold_hours": 0,
                    "by_direction": {
                        "long": {"trades": 0, "wins": 0, "win_rate": 0},
                        "short": {"trades": 0, "wins": 0, "win_rate": 0},
                    },
                    "by_tier": {
                        "strong": {"trades": 0, "wins": 0, "win_rate": 0},
                        "medium": {"trades": 0, "wins": 0, "win_rate": 0},
                        "weak": {"trades": 0, "wins": 0, "win_rate": 0},
                    },
                    "by_close_reason": {},
                }

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

            if len(pnls) > 1:
                import statistics
                mean = statistics.mean(pnls)
                stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
                sharpe_ratio = (mean / stdev * (252 ** 0.5)) if stdev > 0 else 0
            else:
                sharpe_ratio = 0

            best_trade = max(trades, key=lambda t: t.pnl_pct)
            worst_trade = min(trades, key=lambda t: t.pnl_pct)

            hold_times = []
            for t in trades:
                if t.exit_time and t.entry_time:
                    hours = (t.exit_time - t.entry_time) / 1000 / 3600
                    hold_times.append(hours)
            avg_hold_hours = sum(hold_times) / len(hold_times) if hold_times else 0

            # By direction
            def _dir_stats(direction):
                dt = [t for t in trades if t.direction == direction]
                dw = sum(1 for t in dt if t.pnl_pct > 0)
                return {"trades": len(dt), "wins": dw, "win_rate": (dw / len(dt) * 100) if dt else 0}

            # By tier
            def _tier_stats(tier):
                dt = [t for t in trades if t.signal_tier == tier]
                dw = sum(1 for t in dt if t.pnl_pct > 0)
                return {"trades": len(dt), "wins": dw, "win_rate": round(dw / len(dt) * 100, 2) if dt else 0}

            # By close reason
            reason_counts: Dict[str, int] = {}
            for t in trades:
                r = t.close_reason or "unknown"
                reason_counts[r] = reason_counts.get(r, 0) + 1

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
                "symbol": best_trade.symbol, "direction": best_trade.direction,
                "pnl_pct": round(best_trade.pnl_pct, 2), "pnl_usd": round(best_trade.pnl_usd, 2),
                "tier": best_trade.signal_tier, "reason": best_trade.close_reason,
            },
            "worst_trade": {
                "symbol": worst_trade.symbol, "direction": worst_trade.direction,
                "pnl_pct": round(worst_trade.pnl_pct, 2), "pnl_usd": round(worst_trade.pnl_usd, 2),
                "tier": worst_trade.signal_tier, "reason": worst_trade.close_reason,
            },
            "avg_hold_hours": round(avg_hold_hours, 2),
            "by_direction": {d: _dir_stats(d) for d in ["long", "short"]},
            "by_tier": {t.value: _tier_stats(t.value) for t in SignalTier},
            "by_close_reason": reason_counts,
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

    def close_position(self, trade_id: int, current_price: float, reason: str = "manual") -> Optional[Dict]:
        """Manually close an open position"""
        import time as _time
        with self.store._conn() as conn:
            result = self._close_trade(trade_id, current_price, int(_time.time() * 1000), reason, conn)
        return asdict(result) if result else None

    def _row_to_trade(self, row) -> TradeData:
        """Convert database row to TradeData, filtering extra columns"""
        data = dict(row)
        data.pop('created_at', None)
        # Handle missing columns gracefully for existing rows
        if 'signal_tier' not in data or not data['signal_tier']:
            data['signal_tier'] = 'medium'
        if 'trailing_activated' not in data:
            data['trailing_activated'] = 0
        if 'trailing_stop_price' not in data:
            data['trailing_stop_price'] = 0.0
        return TradeData(**data)

    def _get_trade_by_id(self, trade_id: int) -> Optional[TradeData]:
        """Get trade by ID"""
        with self.store._conn() as conn:
            row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
            if row:
                return self._row_to_trade(row)
        return None

    def format_stats_message(self, stats: Dict) -> str:
        """Format statistics as Telegram message"""
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
            lines.append(f"  ✅ Best: {bt['symbol']} {bt['tier']} {bt['pnl_pct']:+.2f}% (${bt['pnl_usd']:+.2f})")
        if stats['worst_trade']:
            wt = stats['worst_trade']
            lines.append(f"  ❌ Worst: {wt['symbol']} {wt['tier']} {wt['pnl_pct']:+.2f}% (${wt['pnl_usd']:+.2f})")

        lines.extend([
            f"",
            f"📊 *Avg Win/Loss*",
            f"  Avg Win: {stats['avg_win_pct']:+.2f}%",
            f"  Avg Loss: {stats['avg_loss_pct']:+.2f}%",
            f"",
            f"⏱️ Avg Hold: {stats['avg_hold_hours']:.1f}h",
            f"📈 Sharpe: {stats['sharpe_ratio']:.2f}",
        ])

        # Tier breakdown
        if stats.get('by_tier'):
            lines.append(f"\n📊 *By Tier*")
            for tier_name in ('strong', 'medium', 'weak'):
                t = stats['by_tier'].get(tier_name, {})
                if t.get('trades', 0) > 0:
                    lines.append(f"  {tier_name.title()}: {t['trades']} trades, {t['win_rate']}% win")

        # Close reason breakdown
        if stats.get('by_close_reason'):
            lines.append(f"\n📋 *Exit Reasons*")
            for reason, count in sorted(stats['by_close_reason'].items(), key=lambda x: -x[1]):
                lines.append(f"  {reason}: {count}")

        return "\n".join(lines)
