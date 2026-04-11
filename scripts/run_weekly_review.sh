#!/bin/bash
# Weekly Paper Trading & ML Performance Review
# Run manually: ./scripts/run_weekly_review.sh
# Or schedule via cron: 0 9 * * 1 /path/to/whale-alert-service/scripts/run_weekly_review.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
REPORT_DIR="$PROJECT_DIR/.omc/reports"

echo "========================================="
echo "Weekly Whale Alert Service Review"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="
echo ""

cd "$PROJECT_DIR"

# Create report directory if it doesn't exist
mkdir -p "$REPORT_DIR"

# Generate report filename with current date
REPORT_FILE="$REPORT_DIR/weekly_review_$(date '+%Y-%m-%d').md"

echo "Generating report: $REPORT_FILE"
echo ""

# Start report header
cat > "$REPORT_FILE" << EOF
# Weekly Performance Report
**Date:** $(date '+%Y-%m-%d %H:%M:%S')
**Type:** Automated Weekly Review
**Service:** Whale Alert v4

---

## Executive Summary

EOF

# 1. Paper Trading Stats
echo "📊 Analyzing paper trading..."
python3 -c "
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('whale_alert.db')
conn.row_factory = sqlite3.Row

# Overall stats
trades = conn.execute('''
    SELECT status, COUNT(*) as c,
           SUM(CASE WHEN pnl_usd IS NOT NULL THEN pnl_usd ELSE 0 END) as total_pnl
    FROM paper_trades
    GROUP BY status
''').fetchall()

print('### Paper Trading Overview\\n')
print('| Status | Count | Total PnL |')
print('|--------|-------|----------|')
for t in trades:
    status = t['status']
    count = t['c']
    pnl = t['total_pnl'] if t['total_pnl'] else 0
    print(f'| {status} | {count} | \${pnl:,.2f} |')

# By tier
print('\\n### Performance by Tier\\n')
tiers = conn.execute('''
    SELECT
        CASE
            WHEN alert_score >= 60 THEN 'STRONG'
            WHEN alert_score >= 53 THEN 'MEDIUM'
            WHEN alert_score >= 40 THEN 'WEAK'
            ELSE 'OTHER'
        END as tier,
        COUNT(*) as trades,
        SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
        AVG(CASE WHEN pnl_usd IS NOT NULL THEN pnl_usd ELSE 0 END) as avg_pnl,
        SUM(CASE WHEN pnl_usd IS NOT NULL THEN pnl_usd ELSE 0 END) as total_pnl
    FROM paper_trades
    WHERE status = 'closed'
    GROUP BY tier
    ORDER BY tier DESC
''').fetchall()

print('| Tier | Trades | Wins | Losses | Win Rate | Avg PnL | Total PnL |')
print('|------|--------|------|--------|----------|---------|-----------|')
for t in tiers:
    if t['trades'] > 0:
        win_rate = t['wins'] / t['trades'] * 100
        print(f'| {t[\"tier\"]} | {t[\"trades\"]} | {t[\"wins\"]} | {t[\"losses\"]} | {win_rate:.1f}% | \${t[\"avg_pnl\"]:.2f} | \${t[\"total_pnl\"]:,.2f} |')

# Recent trades (last 7 days)
cutoff = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
recent = conn.execute('''
    SELECT COUNT(*) as c FROM paper_trades
    WHERE entry_time > ?
''', (cutoff,)).fetchone()[0]

print(f'\\n**Recent Activity (7 days):** {recent} trades')

conn.close()
" >> "$REPORT_FILE"

echo "✅ Trading stats collected"
echo ""

# 2. ML System Status
echo "🧠 Analyzing ML system..."
python3 -c "
import sqlite3

conn = sqlite3.connect('whale_alert.db')
conn.row_factory = sqlite3.Row

print('### ML System Status\\n')

# Sample counts
ml_samples = conn.execute('SELECT COUNT(*) as c FROM ml_training_samples').fetchone()[0]
verified = conn.execute('SELECT COUNT(*) as c FROM ml_training_samples WHERE label_verified=1').fetchone()[0]
pending = conn.execute('SELECT COUNT(*) as c FROM pending_ml_samples').fetchone()[0]
unlabeled = ml_samples - verified

print(f'- **Total ML Samples:** {ml_samples:,}')
print(f'- **Verified Labels:** {verified:,} ({verified/ml_samples*100:.1f}%)')
print(f'- **Pending Samples:** {pending:,}')
print(f'- **Unlabeled Samples:** {unlabeled:,}')

# Pump events
pumps = conn.execute('SELECT COUNT(*) as c FROM pump_events').fetchone()[0]
print(f'\\n### Pump Detection\\n')
print(f'- **Total Pump Events:** {pumps}')

if pumps > 0:
    latest = conn.execute('SELECT * FROM pump_events ORDER BY detected_at DESC LIMIT 1').fetchone()
    from datetime import datetime
    dt = datetime.fromtimestamp(latest['detected_at']/1000)
    print(f'- **Latest Pump:** {latest[\"symbol\"]} ({dt.strftime(\"%Y-%m-%d %H:%M\")})')

# Model info
try:
    import pickle
    import os
    model_path = 'models/gbdt_predictor.pkl'
    if os.path.exists(model_path):
        with open(model_path, 'rb') as f:
            model = pickle.load(f)
        version = getattr(model.predictor, '_model_version', 'unknown')
        print(f'\\n### Model\\n')
        print(f'- **Version:** v{version}')
        print(f'- **File:** {model_path}')
    else:
        print('\\n### Model\\n')
        print('- **Status:** Not trained yet')
except:
    print('\\n### Model\\n')
    print('- **Status:** Error loading model info')

conn.close()
" >> "$REPORT_FILE"

echo "✅ ML stats collected"
echo ""

# 3. System Health
echo "🏥 Analyzing system health..."
python3 -c "
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('whale_alert.db')
conn.row_factory = sqlite3.Row

print('### System Health\\n')

# Recent scan activity
cutoff = int((datetime.now() - timedelta(hours=2)).timestamp() * 1000)
recent_snapshots = conn.execute('SELECT COUNT(*) as c FROM snapshots WHERE timestamp > ?', (cutoff,)).fetchone()[0]

if recent_snapshots > 0:
    print(f'✅ **Scanning Active:** {recent_snapshots} snapshots in last 2 hours')
else:
    print('⚠️ **Scanning Stalled:** No snapshots in last 2 hours')

# Open positions
open_positions = conn.execute('SELECT COUNT(*) as c FROM paper_trades WHERE status=\"open\"').fetchone()[0]
print(f'\\n### Active Trading\\n')
print(f'- **Open Positions:** {open_positions}')

conn.close()
" >> "$REPORT_FILE"

echo "✅ System health check complete"
echo ""

# 4. Recommendations
echo "💭 Generating recommendations..."
cat >> "$REPORT_FILE" << EOF

---

## Recommendations & Action Items

### Trading System
- [ ] Monitor trade count progress toward 50 trades/tier validation
- [ ] Verify no eviction bugs (check for \$0 PnL trades)
- [ ] Check if gap protection is working (no losses >12% overnight)
- [ ] Review if parameter adjustments needed

### ML System
- [ ] Monitor pending samples growth (should increase every scan)
- [ ] After 24h, verify first batch of labeled samples
- [ ] When 1,000+ verified samples collected, retrain model
- [ ] Expected AUC drop from ≈1.0 → 0.65-0.80 (healthy sign)

### Next Review
**Scheduled:** Next Monday 9:00 AM
**Trigger manually:** Run \`./scripts/run_weekly_review.sh\`

---

**Report Status:** ✅ COMPLETE
**Auto-generated:** $(date '+%Y-%m-%d %H:%M:%S')
EOF

echo "✅ Recommendations added"
echo ""

echo "========================================="
echo "✅ Weekly review complete!"
echo "📄 Report saved to: $REPORT_FILE"
echo ""
echo "View report:"
echo "  cat $REPORT_FILE"
echo "  or"
echo "  less $REPORT_FILE"
echo "========================================="
