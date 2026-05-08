#!/usr/bin/env python3
"""
Threshold Performance Monitor
Tracks the effectiveness of the lowered threshold (35 vs 40)

Run daily or weekly to assess impact:
    python3 scripts/monitor_threshold_performance.py

Or schedule via cron:
    0 9 * * * /path/to/whale-alert-service/scripts/monitor_threshold_performance.py >> logs/threshold_monitor.log 2>&1
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class ThresholdMonitor:
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

    def analyze_alerts_by_score_range(self, hours_back=24):
        """Analyze alerts grouped by score range"""

        # Get recent alerts
        cutoff = int((datetime.now() - timedelta(hours=hours_back)).timestamp() * 1000)

        # Alerts from database (need to check what table tracks this)
        # For now, use snapshots that crossed threshold
        alerts = self.conn.execute('''
            SELECT
                symbol,
                timestamp,
                control_score,
                phase,
                pump_probability
            FROM snapshots
            WHERE control_score >= 35
            AND timestamp > ?
            ORDER BY control_score DESC
        ''', (cutoff,)).fetchall()

        if not alerts:
            return None

        # Group by score range
        score_ranges = {
            '35-39 (NEW)': [],
            '40-49 (OLD)': [],
            '50+ (HIGH)': []
        }

        for alert in alerts:
            score = alert['control_score']
            if 35 <= score <= 39:
                score_ranges['35-39 (NEW)'].append(alert)
            elif 40 <= score <= 49:
                score_ranges['40-49 (OLD)'].append(alert)
            else:
                score_ranges['50+ (HIGH)'].append(alert)

        return score_ranges

    def check_outcomes(self, hours_back=48):
        """Check what happened to alerts (pump or not)"""

        cutoff = int((datetime.now() - timedelta(hours=hours_back)).timestamp() * 1000)

        # Get alerts that are old enough to have 24h outcome
        alerts = self.conn.execute('''
            SELECT
                symbol,
                timestamp,
                control_score,
                phase
            FROM snapshots
            WHERE control_score >= 35
            AND timestamp < ?
            ORDER BY timestamp DESC
        ''', (cutoff,)).fetchall()

        outcomes = {
            'pumps': [],
            'false_positives': [],
            'pending': []
        }

        for alert in alerts:
            # Check if there's a pump event for this symbol
            pump = self.conn.execute('''
                SELECT pump_pct
                FROM pump_events
                WHERE symbol = ?
                AND detected_at > ?
            ''', (alert['symbol'], alert['timestamp'])).fetchone()

            if pump and pump['pump_pct'] >= 20:
                outcomes['pumps'].append({
                    'symbol': alert['symbol'],
                    'score': alert['control_score'],
                    'gain': pump['pump_pct']
                })
            elif pump and pump['pump_pct'] < 20:
                outcomes['false_positives'].append({
                    'symbol': alert['symbol'],
                    'score': alert['control_score'],
                    'gain': pump['pump_pct']
                })
            else:
                # Check false_positives table
                fp = self.conn.execute('''
                    SELECT actual_change_24h, max_change_24h
                    FROM false_positives
                    WHERE symbol = ?
                    AND alert_timestamp > ?
                ''', (alert['symbol'], alert['timestamp'])).fetchone()

                if fp:
                    if fp['max_change_24h'] >= 20:
                        outcomes['pumps'].append({
                            'symbol': alert['symbol'],
                            'score': alert['control_score'],
                            'gain': fp['max_change_24h']
                        })
                    else:
                        outcomes['false_positives'].append({
                            'symbol': alert['symbol'],
                            'score': alert['control_score'],
                            'gain': fp['actual_change_24h']
                        })
                else:
                    outcomes['pending'].append(alert)

        return outcomes

    def calculate_metrics(self, outcomes):
        """Calculate performance metrics"""

        pumps = outcomes['pumps']
        false_positives = outcomes['false_positives']

        total = len(pumps) + len(false_positives)
        if total == 0:
            return None

        pump_rate = len(pumps) / total * 100
        avg_pump_gain = sum(p['gain'] for p in pumps) / len(pumps) if pumps else 0
        avg_fp_gain = sum(fp['gain'] for fp in false_positives) / len(false_positives) if false_positives else 0

        return {
            'total_alerts': total,
            'pumps': len(pumps),
            'false_positives': len(false_positives),
            'pump_rate': pump_rate,
            'avg_pump_gain': avg_pump_gain,
            'avg_fp_gain': avg_fp_gain
        }

    def compare_with_baseline(self):
        """Compare current performance with baseline (threshold=40)"""

        # Get baseline stats from before the change
        # From our analysis: 79 false positives, 0 pumps at threshold 40

        return {
            'baseline_fp': 79,
            'baseline_pumps': 0,
            'baseline_pump_rate': 0.0,
            'note': 'Baseline from false_positives table analysis'
        }


def format_report(score_ranges, outcomes, metrics, baseline):
    """Generate a human-readable report"""

    lines = []
    lines.append("=" * 70)
    lines.append("THRESHOLD PERFORMANCE MONITOR")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Current Threshold: 35 (lowered from 40 on 2026-04-11)")
    lines.append("=" * 70)
    lines.append("")

    # Score distribution
    if score_ranges:
        lines.append("📊 ALERT DISTRIBUTION (Last 24h)")
        lines.append("-" * 70)

        for range_name, alerts in score_ranges.items():
            count = len(alerts)
            if count > 0:
                avg_score = sum(a['control_score'] for a in alerts) / count
                lines.append(f"  {range_name:20s}: {count:3d} alerts (avg score: {avg_score:.1f})")
            else:
                lines.append(f"  {range_name:20s}:   0 alerts")
        lines.append("")

    # Outcomes
    if outcomes:
        total = len(outcomes['pumps']) + len(outcomes['false_positives'])

        lines.append("🎯 ALERT OUTCOMES (Last 48h, resolved)")
        lines.append("-" * 70)
        lines.append(f"  Total Resolved:  {total}")
        lines.append(f"  Pumps Detected:   {len(outcomes['pumps'])}")
        lines.append(f"  False Positives:  {len(outcomes['false_positives'])}")
        lines.append(f"  Pending:          {len(outcomes['pending'])}")
        lines.append("")

        # Show recent pumps
        if outcomes['pumps']:
            lines.append("💰 RECENT PUMPS DETECTED:")
            lines.append("-" * 70)
            for pump in outcomes['pumps'][:5]:
                lines.append(f"  {pump['symbol']:15s} score={pump['score']:2d} → +{pump['gain']:.1f}%")
            if len(outcomes['pumps']) > 5:
                lines.append(f"  ... and {len(outcomes['pumps']) - 5} more")
            lines.append("")

    # Metrics
    if metrics:
        lines.append("📈 PERFORMANCE METRICS")
        lines.append("-" * 70)
        lines.append(f"  Pump Detection Rate: {metrics['pump_rate']:.1f}%")
        lines.append(f"  Avg Pump Gain:       {metrics['avg_pump_gain']:.1f}%")
        lines.append(f"  Avg FP Gain:          {metrics['avg_fp_gain']:.2f}%")
        lines.append("")

        # Comparison with baseline
        if baseline:
            lines.append("📊 COMPARISON WITH BASELINE (threshold=40)")
            lines.append("-" * 70)
            lines.append(f"  Baseline FP rate:   {baseline['baseline_fp']} false positives")
            lines.append(f"  Baseline pumps:     {baseline['baseline_pumps']} pumps")
            lines.append(f"  Baseline pump rate: {baseline['baseline_pump_rate']:.1f}%")
            lines.append("")

            # Improvement
            if metrics['pump_rate'] > baseline['baseline_pump_rate']:
                improvement = metrics['pump_rate'] - baseline['baseline_pump_rate']
                lines.append(f"  ✅ Improvement:      +{improvement:.1f}% pump detection rate")
            else:
                decline = baseline['baseline_pump_rate'] - metrics['pump_rate']
                lines.append(f"  ⚠️  Decline:          -{decline:.1f}% pump detection rate")
            lines.append("")

    # Recommendations
    lines.append("💡 RECOMMENDATIONS")
    lines.append("-" * 70)

    if metrics:
        if metrics['pump_rate'] < 5:
            lines.append("  ⚠️  Pump rate < 5% - Consider:")
            lines.append("     - Lowering threshold further (to 30)")
            lines.append("     - Reviewing signal definitions")
            lines.append("     - Checking ML labeling pipeline")
        elif metrics['pump_rate'] > 20:
            lines.append("  ✅ Pump rate > 20% - Good!")
            lines.append("     - Monitor if sustainable")
            lines.append("     - Consider raising threshold if FP rate too high")
        else:
            lines.append("  ✅ Pump rate in acceptable range (5-20%)")
            lines.append("     - Continue monitoring")

        if metrics['avg_fp_gain'] > 2:
            lines.append("  ⚠️  Avg FP gain > 2% - False positives gaining significant value")
            lines.append("     - May need to adjust what constitutes a 'pump'")
            lines.append("     - Consider 15% pump threshold instead of 20%")

    lines.append("")
    lines.append("=" * 70)
    lines.append("Report generated by scripts/monitor_threshold_performance.py")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    monitor = ThresholdMonitor()

    with monitor:
        # Get data
        score_ranges = monitor.analyze_alerts_by_score_range(hours_back=24)
        outcomes = monitor.check_outcomes(hours_back=48)
        metrics = monitor.calculate_metrics(outcomes) if outcomes else None
        baseline = monitor.compare_with_baseline()

        # Generate report
        report = format_report(score_ranges, outcomes, metrics, baseline)

        print(report)

        # Save to file
        report_dir = PROJECT_ROOT / ".omc" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        report_file = report_dir / f"threshold_monitor_{datetime.now().strftime('%Y-%m-%d')}.md"

        with open(report_file, 'w') as f:
            f.write(report)

        print()
        print(f"📄 Report saved to: {report_file}")

        # Exit with status code
        if metrics and metrics['pump_rate'] < 5:
            return 1  # Warning: low pump rate
        return 0


if __name__ == '__main__':
    sys.exit(main())
