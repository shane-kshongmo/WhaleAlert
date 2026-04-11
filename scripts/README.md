# Paper Trading Review Scripts

## Automated Weekly Review

### Script: `run_weekly_review.sh`

**Purpose:** Automated weekly analysis of paper trading and ML system performance

**What it does:**
- 📊 Analyzes paper trading stats (PnL, win rate, trades per tier)
- 🧠 Checks ML system health (samples, labels, pump events)
- 🏥 System health check (scanning activity, open positions)
- 💭 Generates recommendations and action items

**Output:** `.omc/reports/weekly_review_YYYY-MM-DD.md`

---

## How to Use

### Option 1: Run Manually (Anytime)

```bash
cd /mnt/d/finance/whale-alert-service
./scripts/run_weekly_review.sh
```

### Option 2: Schedule with Cron (Recommended)

**Edit your crontab:**
```bash
crontab -e
```

**Add this line for weekly execution every Monday at 9am:**
```bash
0 9 * * 1 /mnt/d/finance/whale-alert-service/scripts/run_weekly_review.sh >> /mnt/d/finance/whale-alert-service/logs/review_cron.log 2>&1
```

**Cron format explained:**
- `0 9 * * 1` = At 9:00 AM on Monday
- `>> logs/review_cron.log 2>&1` = Append output to log file

**To verify cron is set:**
```bash
crontab -l
```

### Option 3: Quick Status Check (Daily)

For a quick daily check without the full analysis:

```bash
cd /mnt/d/finance/whale-alert-service
python3 -c "
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('whale_alert.db')

# Quick stats
trades = conn.execute('SELECT COUNT(*) FROM paper_trades WHERE status=\"closed\"').fetchone()[0]
pnl = conn.execute('SELECT SUM(pnl_usd) FROM paper_trades WHERE status=\"closed\"').fetchone()[0] or 0
pending_ml = conn.execute('SELECT COUNT(*) FROM pending_ml_samples').fetchone()[0]

print(f'Trades: {trades}')
print(f'PnL: \${pnl:,.2f}')
print(f'Pending ML samples: {pending_ml}')
"
```

---

## Review Schedule

| Review Type | Frequency | Trigger |
|-------------|-----------|---------|
| **Full Weekly** | Every Monday 9am | Automated (cron) |
| **Quick Status** | Anytime | Manual command |
| **On-Demand** | When needed | `./scripts/run_weekly_review.sh` |

---

## What to Look For in Reports

### 🟢 Good Signs
- Pending ML samples increasing (collecting data)
- Win rate > 50%
- Positive total PnL
- Pump events being recorded
- Verified ML samples growing

### 🟡 Warning Signs
- Win rate < 40% (but > 20 trades → still random)
- Large single loss dominating tier stats
- Pending samples stuck at same number
- No pump events in weeks

### 🔴 Bad Signs
- Multiple \$0 PnL evictions (eviction bug)
- Loss > 12% overnight (gap protection failure)
- AUC still ≈1.0 after labels verified (model overfitting)
- Service not scanning (check logs)

---

## Viewing Past Reports

```bash
# List all reports
ls -lh .omc/reports/weekly_review_*.md

# View most recent
cat .omc/reports/weekly_review_$(date +%Y-%m-%d).md

# View with less (for long reports)
less .omc/reports/weekly_review_*.md
```

---

## Troubleshooting

### Script not running from cron
- **Check path:** Use absolute path `/mnt/d/finance/whale-alert-service/scripts/run_weekly_review.sh`
- **Check permissions:** `ls -l scripts/run_weekly_review.sh` (should show `-rwxr-xr-x`)
- **Check cron logs:** `tail -f logs/review_cron.log`

### Python module errors
- **Use venv:** Update script to use `/mnt/d/finance/whale-alert-service/venv/bin/python`
- **Install deps:** `./venv/bin/pip install httpx`

### Database locked
- **Normal if service is running** - script uses shared lock
- **Wait and retry** if needed

---

## Next Steps After Review

1. **If 50+ trades/tier collected:**
   - Run backtest validation
   - Consider parameter adjustments
   - Statistical significance testing

2. **If ML issues found:**
   - Fix data collection (pending samples not growing)
   - Verify pump detection (0 pump events = problem)
   - Retrain model with verified labels

3. **If trading issues found:**
   - Fix bugs immediately (eviction, gap protection)
   - Adjust parameters if validated by backtest
   - Monitor for next week

---

**Last Updated:** 2026-04-11
**Status:** ✅ Active
