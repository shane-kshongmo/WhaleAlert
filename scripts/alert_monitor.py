#!/usr/bin/env python3
"""
Real-time Alert Monitoring for Whale Alert Service

Checks for immediate danger signals:
- 4+ consecutive losses (streak alert)
- Single loss > $100 or > -10% (large loss alert)
- No scans for 2+ hours (service down alert)
- Pending ML samples not growing (data collection broken)

Usage:
    python3 scripts/alert_monitor.py        # One-time check
    python3 scripts/alert_monitor.py --watch  # Continuous monitoring mode
"""

import sqlite3
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

class AlertMonitor:
    def __init__(self, db_path: str = PROJECT_ROOT / "whale_alert.db"):
        self.db_path = db_path
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def check_consecutive_losses(self):
        """Check for 4+ consecutive losses"""
        cursor = self.conn.execute("""
            SELECT entry_time, exit_time, symbol, pnl_usd, pnl_pct, close_reason
            FROM paper_trades
            WHERE status = 'closed'
            ORDER BY exit_time DESC
            LIMIT 10
        """)

        trades = cursor.fetchall()
        consecutive_losses = 0
        consecutive_wins = 0

        for trade in trades:
            if trade['pnl_usd'] < 0:
                consecutive_losses += 1
                consecutive_wins = 0

                if consecutive_losses >= 4:
                    return {
                        'severity': 'CRITICAL',
                        'type': 'consecutive_losses',
                        'message': f"🔴 {consecutive_losses} consecutive losses detected!",
                        'details': f"Latest: {trade['symbol']} ${trade['pnl_usd']:.2f} ({trade['pnl_pct']:.1f}%)",
                        'trades': trades[:consecutive_losses]
                    }
            else:
                consecutive_wins += 1
                consecutive_losses = 0

        # Also check for 8+ consecutive wins (possible overfitting)
        if consecutive_wins >= 8:
            return {
                'severity': 'WARNING',
                'type': 'consecutive_wins',
                'message': f"🟡 {consecutive_wins} consecutive wins - check for overfitting?",
                'details': "Win streak may indicate lucky market conditions",
                'trades': trades[:consecutive_wins]
            }

        return None

    def check_large_single_loss(self):
        """Check for single loss > $100 or > -10%"""
        cursor = self.conn.execute("""
            SELECT entry_time, exit_time, symbol, pnl_usd, pnl_pct, close_reason
            FROM paper_trades
            WHERE status = 'closed'
            AND pnl_usd < 0
            ORDER BY exit_time DESC
            LIMIT 1
        """)

        trade = cursor.fetchone()

        if trade:
            alerts = []

            # Check dollar amount threshold
            if trade['pnl_usd'] <= -100:
                alerts.append({
                    'severity': 'CRITICAL',
                    'type': 'large_loss_usd',
                    'message': f"🔴 Large loss detected: ${trade['pnl_usd']:.2f}",
                    'details': f"{trade['symbol']} {trade['pnl_pct']:.1f}% (${trade['pnl_usd']:.2f}) - Check gap protection",
                    'trade': trade
                })

            # Check percentage threshold
            if trade['pnl_pct'] <= -10:
                alerts.append({
                    'severity': 'CRITICAL',
                    'type': 'large_loss_pct',
                    'message': f"🔴 Large loss: {trade['pnl_pct']:.1f}%",
                    'details': f"{trade['symbol']} ${trade['pnl_usd']:.2f} - Overnight gap possible",
                    'trade': trade
                })

            if alerts:
                return alerts[0]

        return None

    def check_service_activity(self):
        """Check if service is actively scanning"""
        cutoff = int((datetime.now() - timedelta(hours=2)).timestamp() * 1000)

        cursor = self.conn.execute("""
            SELECT COUNT(*) as c
            FROM snapshots
            WHERE timestamp > ?
        """, (cutoff,))

        recent_scans = cursor.fetchone()['c']

        if recent_scans == 0:
            return {
                'severity': 'CRITICAL',
                'type': 'service_down',
                'message': "🔴 Service stopped scanning!",
                'details': f"No snapshots in last 2 hours - check if main.py is running",
                'action': "Run: ps aux | grep 'python.*main.py' | grep -v grep"
            }

        return None

    def check_ml_data_collection(self):
        """Check if pending ML samples are growing"""
        # Get current count
        current_pending = self.conn.execute(
            "SELECT COUNT(*) as c FROM pending_ml_samples"
        ).fetchone()['c']

        # Check count from 30 minutes ago
        from datetime import timedelta
        cutoff_ago = datetime.now() - timedelta(minutes=30)
        cutoff_ms = int(cutoff_ago.timestamp() * 1000)

        # Check created_at in pending samples (need to handle timestamp format)
        # Assuming created_at is stored as integer milliseconds
        old_pending = self.conn.execute(
            "SELECT COUNT(*) as c FROM pending_ml_samples WHERE created_at < ?",
            (cutoff_ms,)
        ).fetchone()['c']

        # Calculate expected growth (should be ~23 samples every 15 min)
        # 30 min = 2 scans = ~46 samples expected
        expected_min = 40  # Allow some margin

        if old_pending > 0 and current_pending == old_pending:
            # Stagnant for 30+ minutes
            return {
                'severity': 'CRITICAL',
                'type': 'ml_stagnant',
                'message': "🔴 ML data collection stalled!",
                'details': f"Pending samples: {current_pending} (not growing for 30 min)",
                'action': "Check save_ml_sample_pending() in main.py line 158"
            }

        # Also check if pending samples exist at all (after first 10 minutes of runtime)
        service_start_time = datetime.now() - timedelta(minutes=10)
        if service_start_time > datetime(2026, 4, 11, 15, 13, 0):  # After service started
            if current_pending < 10:
                return {
                    'severity': 'WARNING',
                    'type': 'ml_low',
                    'message': f"⚠️ Low pending sample count: {current_pending}",
                    'details': "Expected ~60+ after 10 minutes, check if scan loop running",
                    'action': "Verify save_ml_sample_pending() is being called"
                }

        return None

    def check_zero_pnl_evictions(self):
        """Check for RECENT $0 PnL evictions (eviction bug indicator)

        Only checks evictions from the last 2 hours to avoid historical artifacts.
        """
        cutoff = int((datetime.now() - timedelta(hours=2)).timestamp() * 1000)

        cursor = self.conn.execute("""
            SELECT COUNT(*) as c
            FROM paper_trades
            WHERE status = 'closed'
            AND pnl_usd = 0
            AND close_reason = 'evicted'
            AND exit_time > ?
        """, (cutoff,))

        zero_pnl_evictions = cursor.fetchone()['c']

        if zero_pnl_evictions > 0:
            # Get recent evictions
            latest = self.conn.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'closed'
                AND pnl_usd = 0
                AND close_reason = 'evicted'
                AND exit_time > ?
                ORDER BY exit_time DESC
                LIMIT 3
            """, (cutoff,)).fetchall()

            return {
                'severity': 'CRITICAL',
                'type': 'eviction_bug',
                'message': f"🔴 {zero_pnl_evictions} $0 PnL evictions detected (last 2h)!",
                'details': f"Eviction bug may have returned - check: {latest[0]['symbol'] if latest else 'N/A'}",
                'action': "Review paper_trader.py _try_evict() function"
            }

        return None

    def check_all_alerts(self):
        """Run all checks and return list of alerts"""
        alerts = []

        # Check consecutive losses
        alert = self.check_consecutive_losses()
        if alert:
            alerts.append(alert)

        # Check large single loss
        alert = self.check_large_single_loss()
        if alert:
            alerts.append(alert)

        # Check service activity
        alert = self.check_service_activity()
        if alert:
            alerts.append(alert)

        # Check ML data collection
        alert = self.check_ml_data_collection()
        if alert:
            alerts.append(alert)

        # Check zero PnL evictions
        alert = self.check_zero_pnl_evictions()
        if alert:
            alerts.append(alert)

        return alerts

    def format_alert(self, alert):
        """Format alert for logging"""
        severity_emoji = {
            'CRITICAL': '🔴',
            'WARNING': '🟡',
            'INFO': '🔵'
        }

        emoji = severity_emoji.get(alert['severity'], '⚪')

        lines = [
            f"{emoji} [{alert['type']}] {alert['message']}",
            f"   Details: {alert.get('details', 'N/A')}",
        ]

        if 'action' in alert:
            lines.append(f"   Action: {alert['action']}")

        return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Monitor whale alert service for danger signals')
    parser.add_argument('--watch', action='store_true', help='Continuous monitoring mode (check every 15 min)')
    parser.add_argument('--interval', type=int, default=15, help='Check interval in minutes (default: 15)')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    args = parser.parse_args()

    monitor = AlertMonitor()

    def run_checks():
        with monitor:
            alerts = monitor.check_all_alerts()

            if not alerts:
                print(f"✅ All checks passed - {datetime.now().strftime('%H:%M:%S')}")
                return True  # All good
            else:
                print(f"\n{'='*60}")
                print(f"🚨 ALERTS DETECTED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*60}")

                for i, alert in enumerate(alerts, 1):
                    print()
                    print(f"[{i}/{len(alerts)}]")
                    print(monitor.format_alert(alert))

                print(f"{'='*60}")
                return False  # Alerts found

    if args.once:
        # Run once and exit
        all_good = run_checks()
        sys.exit(0 if all_good else 1)

    elif args.watch:
        # Continuous monitoring mode
        print(f"🔍 Monitoring mode active (checking every {args.interval} min)")
        print(f"   Press Ctrl+C to stop")
        print()

        try:
            while True:
                run_checks()
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            print("\n\n✅ Monitoring stopped")

    else:
        # Default: run once
        all_good = run_checks()
        sys.exit(0 if all_good else 1)


if __name__ == '__main__':
    main()
