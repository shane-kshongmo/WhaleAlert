# ML Model Integrity & Performance Report
**Data Researcher Analysis**
Date: 2026-04-11
Loop: 4 (Post-Feature Addition Analysis)

---

## Executive Summary

🚨 **CRITICAL FINDINGS:**

1. **New features are NOT being used** - The loop 4 additions (time features, market_cap_tier) are defined in code but NOT populated in the database
2. **Model still has perfect AUC ≈ 1.0** - Despite circular labels, performance remains artificially perfect
3. **18 problematic features** - 13 all-zero + 3 constant + 2 mostly-zero
4. **P0 gaps remain unresolved** - Both deferred labeling (P0-1) and pump detection (P0-3) are still broken

**Bottom Line:** The feature additions from loop 4 are INCOMPLETE and NOT improving the model. The model still learns from circular label patterns, not actual predictive features.

---

## 1. Current Model Performance

### Model Versions

| Model ID | Version | Trained At        | Samples | AUC      | Precision | Recall   | CV AUC   |
|----------|---------|-------------------|---------|----------|-----------|----------|----------|
| 3        | 12392   | 2026-04-10 13:21  | 12,392  | **1.000** | 0.972     | 1.000    | 0.9999   |
| 2        | 12442   | 2026-04-10 03:32  | 12,442  | **1.000** | 0.973     | 1.000    | 0.9999   |
| 1        | 12498   | 2026-04-09 16:27  | 12,498  | **1.000** | 0.975     | 1.000    | 0.9999   |

### Key Observations

- **AUC still ≈ 1.0** - No improvement from feature additions
- **Precision dropped** - From 0.975 (v12498) → 0.972 (v12392), slight degradation
- **All models overfit** - Perfect AUC with circular labels is a red flag
- **No new model trained** after loop 4 changes - The latest model (v12392) was trained BEFORE feature additions

### Top 10 Features (Latest Model)

| Rank | Feature                | Importance |
|------|------------------------|------------|
| 1    | price_range_30d        | 222.0      |
| 2    | vol_shrink_ratio       | 194.0      |
| 3    | taker_buy_ratio_7d     | 193.0      |
| 4    | price_range_7d         | 188.0      |
| 5    | avg_trade_size_ratio   | 161.0      |
| 6    | vol_shrink_x_range     | 112.0      |
| 7    | change_7d              | 77.0       |
| 8    | macd_histogram         | 66.0       |
| 9    | rsi_14                 | 43.0       |
| 10   | bb_width               | 35.0       |

**Missing from top features:** All new features from loop 4 (funding_rate, market_cap_tier, time features) are NOT in the top rankings.

---

## 2. Feature Completeness Analysis

### Critical Finding: Features Mismatch

```
Code defines:  47 features
Database has:  42 features
Missing:       5 features (positions 42-46)
```

### All-Zero Features (18 total)

| Position | Feature                | Status                                      |
|----------|------------------------|---------------------------------------------|
| 3        | dim_onchain_flow       | ⚠ Data source missing                       |
| 5        | dim_concentration      | ⚠ Data source missing                       |
| 23       | ma_alignment           | ⚠ Data source missing                       |
| 24       | net_outflow_count      | ⚠ Data source missing                       |
| 25       | top10_holders_pct      | ⚠ Data source missing                       |
| 27       | vol_shrink_x_range     | ⚠ Data source missing                       |
| 30       | rsi_x_change7d         | ⚠ Data source missing                       |
| 31       | concentration_x_vol    | ⚠ Data source missing                       |
| 32       | score_delta_3scan      | ⚠ Data source missing                       |
| 35       | volume_acceleration    | ⚠ Data source missing                       |
| 37       | days_since_last_pump   | ⚠ Pump history not populated               |
| 39       | btc_rsi                | ⚠ BTC data not populated                    |
| **40**  | **log_market_cap_tier**| **🔴 BUG - Loop 4 feature not populated**   |
| **42**  | **hour_cos**           | **🔴 BUG - Loop 4 feature not populated**   |
| **43**  | **is_asia_session**    | **🔴 BUG - Loop 4 feature not populated**   |
| 44       | rt_volume_surge_5m     | ✗ Expected (RT not implemented)             |
| 45       | rt_price_change_5m     | ✗ Expected (RT not implemented)             |
| 46       | rt_bid_ask_imbalance   | ✗ Expected (RT not implemented)             |

### Constant Features (3 total)

| Position | Feature           | Value | Issue                          |
|----------|-------------------|-------|--------------------------------|
| 36       | was_pumped_7d     | 30.0  ⚠ Constant (pump history broken)      |
| 38       | btc_change_24h    | 50.0  ⚠ Constant (BTC data broken)           |
| 41       | hour_sin          | 0.5   ⚠ Constant (time feature broken)       |

---

## 3. New Feature Status (Loop 4 Additions)

### Expected Features (Per Implementation Plan)

| Feature                | Plan Item | Status in Database | Status |
|------------------------|-----------|--------------------|--------|
| dim_funding_rate       | P1-1      | Present (pos 7)     | ✅ Defined but low usage |
| log_market_cap_tier    | P1-3      | **ALL ZERO (pos 40)** | 🔴 NOT POPULATED |
| hour_sin               | P2-3      | **CONSTANT 0.5 (pos 41)** | 🔴 BROKEN |
| hour_cos               | P2-3      | **ALL ZERO (pos 42)** | 🔴 NOT POPULATED |
| is_asia_session        | P2-3      | **ALL ZERO (pos 43)** | 🔴 NOT POPULATED |

### Why New Features Aren't Working

1. **Code defines 47 features** but only **42 are computed**
2. **Last 5 features (pos 42-46) are never populated** in the feature extraction pipeline
3. **dim_funding_rate exists** but has zero importance in the model
4. **Time features (hour_sin, hour_cos, is_asia_session)** are defined but not calculated
5. **log_market_cap_tier** is defined but never computed from volume_24h

---

## 4. Training Samples Analysis

### Sample Statistics

```
Total Samples:        23,531
Unique Symbols:       122
Positive Labels:      5,040 (21.4%)
Negative Labels:      18,491 (78.6%)
Null actual_change:   17,693 (75.2%)
```

### Circular Label Problem (P0-1)

```
All 5,040 positive samples have actual_change = NULL
DB schema changes NOT applied from loop 4
```

**Impact:** The model learns from "pump detected" label (rule-based) instead of actual outcome.

---

## 5. P0 Gaps Impact Assessment

### P0-1: Deferred Labeling System

**Status:** 🔴 NOT FIXED  
**Impact:** CRITICAL

- **100% of positive samples** have `actual_change=NULL`
- Model cannot learn from actual outcomes
- AUC ≈ 1.0 is meaningless - model just memorizes rule patterns

### P0-3: Pump Detection Loop

**Status:** 🔴 NOT FIXED  
**Impact:** HIGH

- **0 pump_events** in database
- Cannot track actual pump occurrences
- `was_pumped_7d` and `days_since_last_pump` features are broken

---

## 6. Recommendations

### Immediate Actions (Priority Order)

1. **🔴 CRITICAL: Fix P0-1 (Deferred Labeling)**
   - Apply DB schema changes from loop 4
   - Backfill actual_change for existing samples
   - Retrain model with real labels

2. **🔴 CRITICAL: Fix P0-3 (Pump Detection Loop)**
   - Implement pump event recording
   - Backfill pump_events table from historical data

3. **🟡 HIGH: Complete Loop 4 Feature Implementation**
   - Fix time feature calculation
   - Implement market_cap_tier from volume_24h

### Should P0 be fixed before adding more features?

**Answer: YES - ABSOLUTELY**

**Rationale:**
1. **Cannot measure improvement** without real labels
2. **New features are wasted** if model learns from circular patterns
3. **AUC ≈ 1.0 is meaningless** - we don't know if model works

---

## 7. Data Quality Dashboard

### Feature Health Summary

```
Total Features:     47
Populated:          29 (62%)
All-Zero:          13 (28%)
Constant:           3 (6%)
Mostly-Zero:        2 (4%)
```

### Sample Quality Summary

```
Total Samples:      23,531
Labeled:            23,531 (100%)
Circular Labels:    17,693 (75.2%)
```

### Model Health Summary

```
Models Trained:     3
Latest Version:     v12392
AUC:                1.000 (⚠ Overfitting)
Feature Count:      42 (should be 47)
```

---

## Conclusion

The ML model is **NOT ready for production** due to circular labels and incomplete feature implementation. 

**Critical path forward:**
1. Fix P0-1 (deferred labeling) → Get real labels
2. Fix P0-3 (pump detection) → Enable historical patterns
3. Complete feature implementation → Actually use new features
4. Retrain and evaluate → Measure real improvement

**DO NOT** add more features until P0 gaps are resolved.

---

**Report prepared by:** Data Researcher Agent  
**Database:** `/mnt/d/finance/whale-alert-service/whale_alert.db`  
**Analysis date:** 2026-04-11
