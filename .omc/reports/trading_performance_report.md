# Paper Trading Performance Report
**Generated:** 2026-04-11  
**Strategy:** Whale Alert v4 — Strategy v2 (Tiered SL/TP, trailing stops, score-deterioration exits)  
**Dataset:** `whale_alert.db / paper_trades` table  
**Capital:** $10,000 paper

---

## 1. Overall Performance Summary

| Metric | Value |
|--------|-------|
| Total trades | 18 |
| Closed trades | 11 |
| Open positions | 7 |
| Win rate | 54.5% |
| Avg PnL% (closed) | +0.17% |
| Total PnL USD (closed) | +$47.50 |
| Avg win | +3.33% |
| Avg loss | -4.53% |
| Win/Loss ratio | 0.74 (unfavorable) |
| Best trade | +8.04% (CFXUSDT eviction_bug_fix) |
| Worst trade | -15.14% (CAKEUSDT stop_loss) |
| Sharpe ratio (annualized, trade-based) | 0.49 |

**Assessment:** Marginally profitable. Win rate >50% is deceptive — the win/loss ratio of 0.74 means losses are larger than wins on average. The negative expectancy is masked by a few large eviction-based wins. With only 11 closed trades, sample size is too small for statistical confidence.

---

## 2. Tier Breakdown

| Tier | N | Win Rate | Avg PnL% | Total PnL USD | Avg Hold |
|------|---|----------|----------|---------------|----------|
| medium | 5 | 60.0% | +0.40% | +$50.06 | 25.85h |
| weak | 5 | 60.0% | +0.01% | +$1.53 | 10.05h |
| strong | 1 | 0.0% | -0.16% | -$4.09 | 0.28h |

**Key observations:**
- **STRONG tier** has zero wins and the only sample exited via `score_deterioration` within 17 minutes — the signal was never given time to develop.
- **MEDIUM tier** drives essentially all dollar PnL ($50 of $47.50 net). However, 1 medium trade (CAKEUSDT, score=50) delivered the worst loss (-15.14%).
- **WEAK tier** barely breaks even (+$1.53 on 5 trades) despite a 60% win rate — small position sizes plus near-zero avg PnL mean weak signals consume slots without meaningful return.

---

## 3. Exit Reason Analysis

| Close Reason | N | Win Rate | Avg PnL% | Total PnL USD |
|--------------|---|----------|----------|---------------|
| evicted | 7 | 71.4% | +1.62% | +$282.73 |
| eviction_bug_fix | 1 | 100% | +8.04% | +$200.98 |
| score_deterioration | 2 | 0.0% | -1.15% | -$57.67 |
| stop_loss | 1 | 0.0% | -15.14% | -$378.53 |

**Critical findings:**
1. **Stop loss was triggered once and destroyed $378** (-15.14% on CAKEUSDT). This single event wipes out all gains from eviction exits combined. The CAKEUSDT loss was previously identified as the "CAKEUSDT lesson" (low volume, overnight gap risk) — but it still happened, suggesting the volume filter may not have been active at trade entry, or score=50 trades need tighter stops.

2. **Eviction exits (+$282 total) are the primary profit driver.** Trades evicted by stronger signals exit while still profitable (+1.62% avg). This is structurally sound — the eviction policy is working as intended.

3. **Score deterioration exits always lose (-$57.67 on 2 trades).** The exits are correct (removing declining signals) but both trades were already underwater before the deterioration trigger fired. This suggests the score deterioration threshold is too slow — by the time score < 25 fires, meaningful drawdown has already occurred.

4. **No trailing_stop or take_profit or timeout exits** in the closed dataset. All exits are either eviction or loss-driven. This means the trailing stop mechanism has never been tested in practice — no trade has run long enough in profit to activate trailing (requires 4–6% gain depending on tier).

---

## 4. Exit Reason × Tier Breakdown

| Tier | Exit Reason | N | Avg PnL% |
|------|-------------|---|----------|
| medium | evicted | 3 | +3.04% |
| medium | stop_loss | 1 | -15.14% |
| medium | eviction_bug_fix | 1 | +8.04% |
| strong | score_deterioration | 1 | -0.16% |
| weak | evicted | 4 | +0.55% |
| weak | score_deterioration | 1 | -2.14% |

---

## 5. Parameter Mismatch: DB Records vs Current TIER_CONFIGS

The SL/TP parameters stored in the database differ from the current `TIER_CONFIGS` in `paper_trader.py`, indicating the configs were changed after trades were opened:

| Tier | DB SL% | DB TP% | Current Config SL% | Current Config TP% |
|------|--------|--------|--------------------|--------------------|
| strong | 8.0 | 15.0 | 6.0 | 18.0 |
| medium | 8.0 | 12.0 | 8.0 | 14.0 |
| weak | 8.0 | 12.0 | 10.0 | 10.0 |

The current `TIER_CONFIGS.WEAK` uses SL=10%, TP=10% (1:1 R:R), while the weak trades in the DB were actually opened with SL=8%, TP=12% (1:1.5 R:R). The WEAK tier was changed to equal R:R at some point.

---

## 6. TIER_CONFIGS Parameter Review & Recommendations

### 6.1 WEAK Tier: TP=10%, SL=10% (1:1 R:R) — SUBOPTIMAL

**Problem:** Equal R:R requires >50% win rate just to break even *before* fees/slippage. Weak signals (score 40–49) are inherently noisy — achieving >55% win rate consistently is unlikely.

**Evidence:** Weak tier delivered only +$1.53 on 5 trades despite 60% win rate. The two losing weak trades lost -2.14% and -0.66% against average wins of ~0.95%.

**Recommendation:** Either:
- (A) **Raise minimum score threshold** from 40 to 48, effectively eliminating the WEAK tier, OR
- (B) **Improve R:R to 1:1.5** (TP=12%, SL=8%) — accept that weak signals need asymmetric payoff to be worth the slot consumption
- Do NOT keep 1:1 R:R for a noisy signal tier

### 6.2 STRONG Tier: score_deterioration after 17 minutes — Trailing Too Slow to Activate

**Problem:** The only strong-tier trade (LTCUSDT, score=75) exited via `score_deterioration` after 17 minutes at -0.16%. With `trailing_activate_pct=4.0`, the trailing stop requires a 4% gain before it activates. Strong signals that start declining early will exit via score deterioration before trailing engages.

**Current params:** SL=6%, TP=18%, trailing_activate=4%, trailing_distance=2.5%

**Recommendation:**
- Lower `trailing_activate_pct` for STRONG from 4.0% to 2.5% — allow trailing to engage sooner on strong signals
- The 2.5% trailing distance is appropriate; don't tighten it further
- TP=18% is correct directionally but rarely reachable — strong signals should run with a tight trailing stop from early profit rather than a hard TP target

### 6.3 MEDIUM Tier SL: The CAKEUSDT Disaster (-15.14%)

**Problem:** CAKEUSDT (score=50, the minimum MEDIUM threshold) hit -15.14% — well past the stated SL of 8%. This implies a gap event bypassed the stop loss (gap_crash protection should have fired at 12%, but it also apparently didn't trigger).

**Investigation:** The trade was opened with SL=8%, TP=12%, but the exit reason is `stop_loss` at -15.14%. This suggests the position monitoring loop missed the 8% stop and the price gapped through it. The `gap_max_loss_pct=12%` should have fired as a backup, but the loss reached 15% — indicating the gap check also failed (price checked after gap, both thresholds already breached simultaneously, close_reason set to `stop_loss` by whichever check ran first).

**Recommendations:**
1. **Score=50 is borderline MEDIUM** — consider raising MEDIUM threshold from score≥50 to score≥53 to reduce inclusion of weak-MEDIUM signals
2. **Gap protection is broken or running too infrequently** — the 12% gap cap should have limited this to -12%, not -15%. Investigate monitoring loop frequency
3. **Add a hard max-loss cap at 10%** regardless of SL config (absolute floor: never lose more than 10% on any single trade)

### 6.4 Eviction Policy: eviction_min_score_gap=10 — CORRECT

**Evidence:** Eviction exits show +1.62% avg PnL, 71% win rate. The policy of requiring a 10-point score gap before evicting is working well — it is not evicting good positions prematurely.

**Recommendation:** Keep `eviction_min_score_gap=10`. Consider logging the new signal score vs evicted score to build a dataset for tuning this further.

### 6.5 Score Deterioration Thresholds: Firing Too Late

**Current:** `score_exit_warning=25` (exit if score<25 AND losing), `score_exit_force=15`

**Evidence:** Both score_deterioration exits were losses (-2.14% and -0.16%). The positions were already losing before the exit fired. Score 25 is extremely low — a signal that has deteriorated to 25 from 43+ has likely already moved significantly against the trade.

**Recommendation:**
- Raise `score_exit_warning` from 25 to 35
- Raise `score_exit_force` from 15 to 25
- This will exit losing positions earlier, before drawdown deepens

### 6.6 min_volume_24h=200K — SHOULD BE RAISED

**Evidence:** CAKEUSDT (the CAKEUSDT lesson) delivered -15.14%, the worst trade. The 200K filter was added post-lesson but the lesson token itself appears to have slipped through (or the filter was not yet active).

**Recommendation:** Raise `min_volume_24h` from 200,000 to 500,000 USDT. This eliminates mid-cap illiquid tokens that can gap significantly overnight. The loss from one illiquid gap trade far exceeds gains from multiple small illiquid winners.

### 6.7 Position Sizing — Currently Capped at $2,500

All open positions show `position_size_usd=$2500` (25% of $10,000 capital cap). The formula `base_risk = capital * 2% * multiplier / sl_pct` is being capped by the 25% max:
- Strong: $10,000 * 2% * 2.0 / 6% = $6,667 → capped to $2,500
- Medium: $10,000 * 2% * 1.5 / 8% = $3,750 → capped to $2,500

**Recommendation:** The cap is working correctly as a safety limiter. With 7 open positions all at $2,500, total exposure is $17,500 on $10,000 capital — **1.75x leverage**. This is aggressive for paper trading. Consider reducing `max_open_positions` from 8 to 5 or reducing `risk_per_trade_pct` from 2.0% to 1.5%.

---

## 7. Summary Recommendations (Prioritized)

| Priority | Change | Rationale |
|----------|--------|-----------|
| P1 | Fix gap protection: investigate why CAKEUSDT hit -15.14% past the 12% gap cap | Broken safety net, highest severity |
| P1 | Raise `min_volume_24h` from 200K to 500K USDT | Prevents illiquid gap trades |
| P2 | Raise MEDIUM tier minimum score from 50 to 53 | Score=50 trades are borderline and drive worst losses |
| P2 | Raise `score_exit_warning` from 25 to 35, `score_exit_force` from 15 to 25 | Earlier exit from deteriorating signals |
| P2 | Fix WEAK tier R:R: change from 1:1 (SL=10%,TP=10%) to 1:1.5 (SL=8%,TP=12%) | 1:1 R:R is unviable for noisy signals |
| P3 | Lower STRONG `trailing_activate_pct` from 4.0% to 2.5% | Engages trailing earlier on strong moves |
| P3 | Reduce `max_open_positions` from 8 to 5 or `risk_per_trade_pct` from 2.0% to 1.5% | 1.75x leverage on paper capital is aggressive |
| P3 | Track trailing stop activations explicitly in DB | Currently zero activations — mechanism is untested |

---

## 8. Data Limitations

- Only 11 closed trades — insufficient for statistical significance on tier-level conclusions
- All trades are `long` direction — no short trades in dataset; short-side parameters untested
- No `trailing_stop`, `take_profit`, or `timeout` exits have occurred — those code paths are empirically untested
- SL/TP params in DB don't match current TIER_CONFIGS for all tiers, indicating parameter drift between trade entry and current config
- The `eviction_bug_fix` close reason suggests a corrected bug during the run — this trade's result should be treated cautiously

