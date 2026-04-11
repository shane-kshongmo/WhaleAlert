# Real-Time Alert Monitoring System

## Overview
Automated danger signal detection for whale alert service. Runs every 15 minutes via cron.

## Alert Types

### 🔴 CRITICAL (Immediate Action Required)

1. **Consecutive Losses** (4+ in a row)
   - Indicates: Strategy breakdown or adverse market conditions
   - Action: Stop trading, investigate parameters

2. **Large Single Loss** (>$100 OR >-10%)
   - Indicates: Gap protection failure or extreme volatility
   - Action: Check gap protection, review overnight gaps

3. **Service Down** (No scans for 2+ hours)
   - Indicates: main.py crashed or stopped
   - Action: Restart service, check logs

4. **ML Data Stagnation** (Pending samples not growing for 30min)
   - Indicates: save_ml_sample_pending() not being called
   - Action: Verify scan loop is running

5. **$0 PnL Evictions** (RECENT: last 2 hours)
   - Indicates: Eviction bug has returned
   - Action: Review paper_trader.py _try_evict() function
   - Note: Historical $0 evictions (before 2026-04-11 15:13) are ignored

### 🟡 WARNING (Monitor Closely)

1. **Consecutive Wins** (8+ in a row)
   - Indicates: Possible overfitting or lucky market conditions
   - Action: Monitor for regression to mean

## Monitoring Scripts

### `scripts/alert_monitor.py`
**Usage:**
```bash
# One-time check
python3 scripts/alert_monitor.py --once

# Continuous monitoring (check every 15 min)
python3 scripts/alert_monitor.py --watch

# Custom interval
python3 scripts/alert_monitor.py --watch --interval 10
```

**What it checks:**
- Paper trading performance (streaks, large losses)
- Service activity (snapshots in last 2 hours)
- ML data collection (pending samples growing)
- Bug indicators ($0 PnL evictions)

## Scheduled Jobs

### Cron Jobs
View current schedule:
```bash
crontab -l
```

**Active Jobs:**
1. **Weekly Review** - Every Monday 9:00 AM
   - Script: `scripts/run_weekly_review.sh`
   - Output: `.omc/reports/weekly_review_YYYY-MM-DD.md`

2. **Alert Monitoring** - Every 15 minutes
   - Script: `scripts/alert_monitor.py`
   - Output: `logs/alert_monitor.log`

## Log Files

- **Alert Monitor Log:** `logs/alert_monitor.log`
- **Weekly Review Cron Log:** `logs/review_cron.log`
- **Service Output Log:** `logs/service_output.log`

## Current Status

✅ **All systems operational:**
- Service actively scanning (240 snapshots in last 2 hours)
- Alert monitoring scheduled (every 15 min)
- Weekly reviews scheduled (Mondays 9am)
- Historical $0 evictions filtered (pre-fix artifacts only)

## Thresholds Documentation

See: `.omc/docs/alert_thresholds.md`

**Key thresholds:**
- Win rate alerts: DISABLED until 25+ trades/tier
- Streak alerts: ACTIVE NOW (4+ losses)
- Large loss alerts: ACTIVE NOW (>$100 or >-10%)
- System health alerts: ACTIVE NOW
