# Weekly Performance Report
**Date:** 2026-04-11 15:36:15
**Type:** Automated Weekly Review
**Service:** Whale Alert v4

---

## Executive Summary

### Paper Trading Overview

| Status | Count | Total PnL |
|--------|-------|----------|
| closed | 13 | $137.43 |
| open | 8 | $0.00 |

### Performance by Tier

| Tier | Trades | Wins | Losses | Win Rate | Avg PnL | Total PnL |
|------|--------|------|--------|----------|---------|-----------|
| WEAK | 10 | 6 | 4 | 60.0% | $5.16 | $51.59 |
| STRONG | 2 | 1 | 1 | 50.0% | $42.92 | $85.84 |
| MEDIUM | 1 | 0 | 1 | 0.0% | $0.00 | $0.00 |

**Recent Activity (7 days):** 21 trades
### ML System Status

- **Total ML Samples:** 20,854
- **Verified Labels:** 0 (0.0%)
- **Pending Samples:** 180
- **Unlabeled Samples:** 20,854

### Pump Detection

- **Total Pump Events:** 1
- **Latest Pump:** TESTUSDT (2026-04-11 08:16)

### Model

- **Status:** Error loading model info
### System Health

✅ **Scanning Active:** 240 snapshots in last 2 hours

### Active Trading

- **Open Positions:** 8

---

## Recommendations & Action Items

### Trading System
- [ ] Monitor trade count progress toward 50 trades/tier validation
- [ ] Verify no eviction bugs (check for $0 PnL trades)
- [ ] Check if gap protection is working (no losses >12% overnight)
- [ ] Review if parameter adjustments needed

### ML System
- [ ] Monitor pending samples growth (should increase every scan)
- [ ] After 24h, verify first batch of labeled samples
- [ ] When 1,000+ verified samples collected, retrain model
- [ ] Expected AUC drop from ≈1.0 → 0.65-0.80 (healthy sign)

### Next Review
**Scheduled:** Next Monday 9:00 AM
**Trigger manually:** Run `./scripts/run_weekly_review.sh`

---

**Report Status:** ✅ COMPLETE
**Auto-generated:** 2026-04-11 15:36:16
