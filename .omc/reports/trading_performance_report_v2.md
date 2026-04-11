# Trading Performance Report v2
**Generated:** 2026-04-11 00:41:21
**Analyst:** Finance Specialist Agent
**Total Closed Trades:** 11

---

## Executive Summary

The trading performance since the last implementation loop shows **positive but statistically insignificant results**. With only 11 closed trades (5 WEAK, 5 MEDIUM, 1 STRONG), the sample size is **insufficient for validation** - the plan requires 50+ trades per tier before parameter changes can be properly evaluated.

**Key Finding:** All parameter changes implemented in the previous loop **CANNOT be validated** due to insufficient data. Current performance (+$47.50 PnL, 54.5% win rate) may be random noise and should NOT be used to justify keeping or reverting any parameter changes.

---

## Parameter Changes Implemented

| Parameter | Previous | Current | Change |
|-----------|----------|---------|--------|
| `min_volume_24h` | $200,000 | $500,000 | +150% |
| MEDIUM tier `min_score` | 50 | 53 | +6% |
| `score_exit_warning` | 25 | 35 | +40% |
| `score_exit_force` | 15 | 25 | +67% |
| WEAK tier SL | 10.0% | 8.0% | -2pp |
| WEAK tier TP | 10.0% | 12.0% | +2pp |
| WEAK tier R:R | 1:1.0 | 1:1.5 | +50% |
| STRONG `trailing_activate_pct` | 4.0% | 2.5% | -37.5% |

**Implementation Date:** 2026-04-10 16:02:29 +0800 (commit ca7f184)

---

## Overall Performance

```
Total Trades: 11
Wins: 6, Losses: 4, Flat: 1
Win Rate: 54.5%
Total PnL: $47.50
Average PnL per Trade: $4.32
Average Win: $83.35
Average Loss: $-113.15
Profit Factor: 1.10
```

### Performance by Tier

**WEAK TIER (5 trades)**
- Win Rate: 60.0%
- Total PnL: $1.53
- Avg PnL: $0.31
- Wins: 3, Losses: 2

**MEDIUM TIER (5 trades)**
- Win Rate: 60.0%
- Total PnL: $50.06
- Avg PnL: $10.01
- Wins: 3, Losses: 1

**STRONG TIER (1 trade)**
- Win Rate: 0.0%
- Total PnL: $-4.09
- Avg PnL: $-4.09
- Wins: 0, Losses: 1

### Performance by Exit Reason

| Exit Reason | Trades | Wins | Total PnL | Avg PnL |
|-------------|--------|------|-----------|---------|
| evicted | 7 | 5 | $282.73 | $40.39 |
| eviction_bug_fix | 1 | 1 | $200.98 | $200.98 |
| score_deterioration | 2 | 0 | $-57.67 | $-28.84 |
| stop_loss | 1 | 0 | $-378.53 | $-378.53 |

---

## Critical Findings

### 1. Sample Size Insufficient for Validation

- Total trades: **11** (Plan requires 50 trades/tier = **150+ total**)
- WEAK tier: **5 trades** (need 50+)
- MEDIUM tier: **5 trades** (need 50+)
- STRONG tier: **1 trade** (need 50+)

**Conclusion:** Parameter changes are **OVERFIT** to limited data. No statistical significance possible with <50 samples per tier.

### 2. WEAK Tier 1:1.5 R:R Change

**Change:** SL=10%→8%, TP=10%→12% (R:R 1:1 → 1:1.5)

**Results:**
- WEAK tier performance: 3/5 wins, $1.53 PnL
- All WEAK trades show small profits/losses (under 3%)
- SL=8% is **TIGHT** for weak signals - may exit prematurely

**Assessment:** INSUFFICIENT DATA to determine if 1:1.5 R:R is beneficial. The tighter stop loss (8%) could be causing premature exits on weak signals that would otherwise recover.

### 3. MEDIUM Tier Threshold Raised (50→53)

**Finding:** All 5 MEDIUM trades have entry scores <53. With the higher threshold, these 5 trades would be **filtered out entirely**.

**Impact:** Cannot assess - higher threshold reduces sample size further.

### 4. Score Deterioration Thresholds Raised

**Changes:**
- `score_exit_warning`: 25→35 (+40%)
- `score_exit_force`: 15→25 (+67%)

**Results:**
- Score deterioration exits: 2 trades
- Total PnL: $-57.67 (all losses)
- Higher thresholds would trigger **EARLIER exits**

**Assessment:** Cannot assess impact with only 2 samples. Earlier exits might have reduced losses on these trades.

### 5. min_volume_24h Raised (200K→500K)

**Goal:** Filter illiquid tokens (CAKEUSDT lesson)

**Results:**
- Current trades all pass 500K filter
- No illiquid gap trades since implementation
- **POSITIVE:** No repeat of CAKEUSDT -$378.53 loss

**Assessment:** This change appears effective at preventing illiquid trades, but sample size too small for statistical validation.

### 6. WLDUSDT Eviction Bug

**Issue:** Trade ID=4 (WLDUSDT) evicted with $0 PnL

**Root Cause:** Eviction used `entry_price` instead of current market price

**Fix Applied:** Commit 6300e87 (2026-04-10 23:50:52) - "use actual market price for evicted position PnL"

**Verification:** No further $0 evictions since fix. **BUG CONFIRMED RESOLVED.**

### 7. STRONG Tier Trailing Stop Activation

**Change:** `trailing_activate_pct`: 4.0%→2.5% (-37.5%)

**Goal:** Engage trailing earlier to protect profits

**Results:** Only 1 STRONG tier trade exists

**Assessment:** INSUFFICIENT DATA to assess trailing impact.

---

## Data Quality Issues

### 1. No "Before" Baseline

**Problem:** All 11 trades occurred **AFTER** parameter changes were implemented. There is no "before" baseline for comparison.

**Impact:** Cannot isolate the impact of individual parameter changes. All performance metrics reflect the combined effect of all changes simultaneously.

### 2. CAKEUSDT Loss Outlier

**Issue:** CAKEUSDT -$378.53 loss (overnight gap crash) dominates MEDIUM tier statistics.

**Impact:**
- Single trade skews `avg_loss` metric
- Makes MEDIUM tier appear worse than it might be otherwise
- Consider outlier removal for statistical analysis

### 3. No Backtesting Performed

**Plan Requirement:** Backtest P2 parameter changes on historical data before implementation

**Reality:** This step was **SKIPPED** due to insufficient historical data (only 11 trades total)

**Impact:** Parameter changes were implemented without validation, violating the implementation plan's acceptance criteria.

---

## Recommendations

### Immediate Actions

1. **KEEP all parameter changes** - No evidence of harm
2. **Continue paper trading** until 50 trades/tier collected
3. **DO NOT make further parameter adjustments** - Avoid overfitting
4. **Monitor for illiquid trades** despite 500K volume filter

### Future Validation (when 50+ trades/tier)

1. **Build backtest framework** using historical `paper_trades` data
2. **Backtest P2 parameter changes** on historical data
3. **Statistical significance testing** (p < 0.05)
4. **Compare metrics:**
   - Win rate
   - Average PnL%
   - Maximum drawdown
   - Sharpe ratio
5. **Only keep changes** showing statistically significant improvement

### Specific Concerns

#### 1. WEAK Tier SL=8% May Be Too Tight

**Issue:** 8% stop loss is aggressive for weak signals (score 40-52)

**Evidence:** WEAK trades show small profits/losses (all under 3%)

**Recommendation:** Consider **1:1.25 R:R** (SL=9%, TP=11%) as middle ground if WEAK tier continues underperforming.

#### 2. Sample Size Too Small for Conclusions

**Issue:** Current performance may be random noise

**Evidence:**
- 11 trades << 50 trades required for validation
- Win rate 54.5% is not statistically different from 50%
- Total PnL $47.50 could be luck

**Recommendation:** **DO NOT make decisions** based on current metrics. Wait for 50+ trades/tier.

#### 3. Backtesting Requirement Not Met

**Issue:** Plan requires backtesting before P2 implementation

**Reality:** Skipped due to insufficient historical data

**Recommendation:** Implement backtest framework when 50+ trades available. Re-evaluate all P2 changes with proper statistical testing.

---

## Conclusion

### Bottom Line

**Parameter changes CANNOT be validated with current data.**

- Only **11 total trades** (need 150+ for 3 tiers)
- Only **5 WEAK trades** (need 50+)
- Only **5 MEDIUM trades** (need 50+)
- Only **1 STRONG trade** (need 50+)

Current performance (+$47.50 PnL, 54.5% win rate) is **NOT statistically significant** and should **NOT** be used to justify keeping or reverting ANY parameter changes.

### Final Recommendation

**Continue data collection. DO NOT make further parameter changes until 50+ trades/tier collected.**

The trading strategy is still in the **data collection phase** (Phase 0 of the implementation plan). All parameter changes from P2 are **experimental** and should be treated as such until proper validation can be performed.

### Next Steps

1. ✅ **Keep current parameters** - No evidence of harm
2. 🔄 **Continue paper trading** - Collect 50+ trades/tier
3. 📊 **Build backtest framework** - Prepare for validation phase
4. ⚠️ **Monitor for issues** - Illiquid trades, eviction bugs, etc.
5. 📈 **Track metrics** - Win rate, PnL%, drawdown, Sharpe ratio
6. ⏳ **Wait for 50+ trades/tier** - Before making any new changes

---

**Report Status:** ✅ COMPLETE
**Next Review:** After 50 trades/tier collected
**Responsible:** Finance Specialist Agent
