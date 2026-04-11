# Pump Pattern Analysis & ML Model Enhancement Report

**Date:** 2026-04-11  
**Analyst:** data-researcher  
**Database:** whale_alert.db

---

## 1. Database Overview

| Table | Rows | Key Finding |
|---|---|---|
| ml_training_samples | 23,411 | 18,371 label=0, 5,040 label=1 |
| snapshots | 7,506 | Multi-symbol score snapshots |
| alerts | 99 | 99 alerts fired, all low-mid scores |
| false_positives | 14 | 14 verified FPs, all got +10 score adj |
| adaptive_thresholds | 14 | 14 symbols have score adjustments |
| pump_events | 0 | No pump events recorded yet |
| crash_events | 0 | No crash events recorded yet |
| learning_log | 0 | No learning cycles have run |
| paper_trades | 18 | 18 paper trades executed |

---

## 2. Historical Pump Events Analysis

### 2.1 Pump Event Table is Empty

The `pump_events` table has **0 rows**. This means the `PumpMonitor` has not detected any confirmed pump events meeting the `PumpDefinition` criteria (24h ≥20% gain with ≥1.5x volume surge). This is a critical gap:

- **No ground truth pump labels exist from real-time monitoring**
- All 5,040 label=1 training samples in `ml_training_samples` appear to be **synthetically labeled** at data collection time (the label was set without actual forward price verification — `actual_change` is NULL for all label=1 samples)
- The `learning_log` is also empty — the LearningEngine's `run_learning_cycle()` has never executed

### 2.2 Label=1 Samples — Structural Issue

```
label=0: 18,371 samples, avg_change=0.011 (actual change populated)
label=1:  5,040 samples, avg_change=None  (actual change is NULL for ALL)
```

This confirms label=1 samples were created with `actual_change=None` — they were labeled based on rule-engine score thresholds at collection time, not on verified forward price movements. The ML model is trained on **proxy labels**, not confirmed pump outcomes.

**Weight structure:** All label=1 samples have `weight=3.0` (uniform positive weighting). Label=0 samples have `weight` ranging 1.0–5.0 (some negatives are upweighted, likely verified non-events).

### 2.3 False Positives (14 verified)

All 14 false positives were triggered at low-to-moderate scores (41–68). Key observations:

| Symbol | Score | Phase | Prob | Actual 24h | Max 24h |
|---|---|---|---|---|---|
| HBARUSDT | 68 | 疑似吸筹 | 48% | +1.37% | +3.34% |
| CAKEUSDT | 50 | 疑似吸筹 | 44% | 0% | +0.74% |
| TRUMPUSDT | 50 | 疑似吸筹 | 46% | -3.41% | +0.43% |
| JSTUSDT | 50 | 疑似吸筹 | 44% | 0% | +0.57% |
| WLDUSDT | 49 | 轻度异常 | 27% | +5.73% | +5.23% |
| FLOKIUSDT | 43 | 轻度异常 | 27% | +6.96% | +3.21% |
| ATOMUSDT | 43 | 轻度异常 | 31% | +2.00% | +2.56% |

**Pattern:** FPs cluster at score 40–50, and even the "hits" (WLDUSDT, FLOKIUSDT) only gained 5–7%, not the 20–30% threshold that would define a real pump. The adaptive threshold system responded by adding +10 score adjustment to all 14 symbols — a blanket response that doesn't address root cause.

### 2.4 Missed Predictions (Cannot Quantify)

Since `pump_events` is empty, we cannot directly measure missed pumps. However, the snapshots table (7,506 rows across ~30 symbols) shows the system was actively monitoring. The highest alert scores were:

- **LTCUSDT**: score=75 (高度控盘), prob=54% — wash trade critical + tight spread + momentum
- **DOGEUSDT**: score=75 (高度控盘), prob=54% — same pattern
- **TONUSDT**: score=70, prob=37%
- **HBARUSDT**: score=68 — this one became a false positive

These high-score alerts (LTC, DOGE) were not followed up with pump confirmation — likely because LTC/DOGE are large-cap coins where "accumulation" signals are market-maker behavior, not manipulation.

---

## 3. ML Model Performance Analysis

### 3.1 Model Versions

Three GBDT models trained, all showing suspiciously perfect metrics:

| Version | Samples | AUC | Precision | Recall | CV AUC |
|---|---|---|---|---|---|
| v12498 | 12,498 | 0.99999 | 0.9747 | 1.000 | 0.99991 |
| v12442 | 12,442 | 0.99999 | 0.9725 | 1.000 | 0.99985 |
| v12392 | 12,392 | 0.99999 | 0.9721 | 1.000 | 0.99987 |

**AUC ~1.0 with Recall=1.0 is a severe overfitting signal.** This is consistent with label leakage: label=1 samples were likely created using `control_score` thresholds, and `control_score` / `dim_accumulation` are features in the model. The model trivially learns "high control_score → label=1" because that's how the labels were assigned.

### 3.2 Top Feature Importance (consistent across all 3 models)

1. `price_range_30d` (233, 227, 222 splits)
2. `vol_shrink_ratio` (210, 208, 194 splits)
3. `taker_buy_ratio_7d` (201, 200, 193 splits)
4. `price_range_7d` (199, 195, 188 splits)
5. `avg_trade_size_ratio` (166, 148, 161 splits)
6. `vol_shrink_x_range` (128, 123, 112 splits)
7. `change_7d` (77, 67, 66 splits)
8. `macd_histogram` (72, 60, 66 splits)
9. `rsi_14` (59, 45, 43 splits)
10. `bb_width` (42, 36, 35 splits)

### 3.3 Feature Mean Comparison: Label=0 vs Label=1

From a 500-sample comparison:

| Feature | Non-Pump Mean | Pump Mean | Delta |
|---|---|---|---|
| dim_accumulation | 3.64 | **20.00** | +16.36 |
| control_score | 16.77 | **35.65** | +18.88 |
| rule_pump_prob | 21.40 | **38.68** | +17.28 |
| dim_momentum | 11.82 | 14.81 | +2.99 |
| vol_shrink_ratio | 1.095 | **0.407** | -0.69 |
| price_range_30d | 0.560 | **0.035** | -0.53 |
| bb_width | 5.86 | **3.40** | -2.46 |
| change_7d | -1.74 | **3.24** | +4.98 |
| score_x_bb_squeeze | 0.843 | **2.361** | +1.52 |

This confirms `dim_accumulation` (缩量横盘 score) is the dominant discriminator, which is circular since labels were likely set when accumulation was detected.

### 3.4 Missing: dim_funding_rate Not in Feature Vector

`WhaleAnalysis.dim_funding_rate` exists and is scored (max=8 pts), but it is **NOT included in the 41 ML features**. The feature vector skips from `dim_spread` (index 6) to `dim_momentum` (index 7), omitting `dim_funding_rate`. This is a concrete gap.

### 3.5 RT Features Mostly Zero

`rt_volume_surge_5m`, `rt_price_change_5m`, `rt_bid_ask_imbalance` (positions 39–41) are present in the feature definition but appear to be 0 in most training samples, suggesting realtime_engine metrics were not populated during the data collection period.

---

## 4. Key Findings Summary

1. **No confirmed pump events** — the ground truth loop is broken; `PumpMonitor` never recorded a pump, so the learning engine never ran
2. **Label leakage** — label=1 samples assigned via rule score thresholds; model has AUC≈1.0 because it learns the labeling function, not actual pump behavior
3. **actual_change is NULL for all positive samples** — no forward price verification occurred
4. **dim_funding_rate excluded from ML features** — scored in whale detector but not fed to model
5. **14 false positives** all received identical +10 score adjustment — no root-cause differentiation
6. **High-score alerts on large caps (LTC, DOGE score=75)** — accumulation signals on liquid coins likely represent normal market-making, not manipulation
7. **RT features (order book, 5m volume)** not populated in training data — 3 features are wasted slots
8. **Time-of-day patterns not captured** — no hour/session feature in the 41 dimensions
9. **Open Interest not tracked** — a key pump leading indicator missing entirely

---

## Model Enhancement Proposals

### Proposal 1: Add dim_funding_rate to Feature Vector

**Problem:** `dim_funding_rate` is scored (0–8 pts) in `WhaleAnalysis` but position 6 in FEATURE_NAMES is `dim_spread` and position 7 is `dim_momentum` — `dim_funding_rate` is skipped entirely.

**Fix in `analysis/ml_predictor.py`:**

```python
# Current FEATURE_NAMES (positions 0-7):
# "dim_accumulation", "dim_large_orders", "dim_imbalance", "dim_onchain_flow",
# "dim_wash_trade", "dim_concentration", "dim_spread", "dim_momentum"

# Change to 42 features — insert dim_funding_rate between dim_spread and dim_momentum:
FEATURE_NAMES = [
    "dim_accumulation",
    "dim_large_orders",
    "dim_imbalance",
    "dim_onchain_flow",
    "dim_wash_trade",
    "dim_concentration",
    "dim_spread",
    "dim_funding_rate",   # ADD: was missing, now position 7
    "dim_momentum",       # shifts to position 8
    "control_score",      # shifts to position 9
    # ... rest shift by 1
]
NUM_FEATURES = 42

# In extract_features(), add after dim_spread:
features = np.array([
    float(wa.dim_accumulation),
    float(wa.dim_large_orders),
    float(wa.dim_imbalance),
    float(wa.dim_onchain_flow),
    float(wa.dim_wash_trade),
    float(wa.dim_concentration),
    float(wa.dim_spread),
    float(wa.dim_funding_rate),   # ADD THIS LINE
    float(wa.dim_momentum),
    # ... rest unchanged
])
```

**Expected impact:** Funding rate is a leading indicator — negative funding (shorts paying longs) often precedes short squeezes/pumps. Estimated +2–5% recall improvement.

---

### Proposal 2: Add Time-of-Day and Market Session Features

**Problem:** No temporal features exist. Pump patterns differ significantly by UTC hour (Asian session 00–08 UTC shows different dynamics than US session 13–21 UTC).

**Add 3 features to `analysis/ml_predictor.py`:**

```python
# Add to FEATURE_NAMES (after btc_rsi, before rt_ features):
"hour_sin",          # sin(2π * hour/24) — cyclical encoding
"hour_cos",          # cos(2π * hour/24) — cyclical encoding  
"is_asia_session",   # 1 if UTC 00–08, else 0

# In extract_features(), compute from timestamp:
import math
hour_utc = (timestamp_ms // 3_600_000) % 24  # timestamp_ms from wa.timestamp
hour_sin = math.sin(2 * math.pi * hour_utc / 24)
hour_cos = math.cos(2 * math.pi * hour_utc / 24)
is_asia = 1.0 if 0 <= hour_utc < 8 else 0.0

# Add to features array:
float(hour_sin),
float(hour_cos),
float(is_asia),
```

Update `NUM_FEATURES = 44` (or 45 with Proposal 1 applied).

**Expected impact:** Models trained on session-aware features can learn that accumulation signals during low-liquidity Asia hours are more predictive than identical signals during high-volume US hours.

---

### Proposal 3: Fix Label Generation — Use Forward Price Verification

**Problem:** All 5,040 label=1 samples have `actual_change=None`. Labels were assigned at collection time without verifying whether the token actually pumped 24h later. This creates circular training data.

**Fix in the data collection loop (wherever `ml_training_samples` is written):**

```python
# Current (broken) pattern — labels assigned immediately:
label = 1 if whale_analysis.control_score >= SOME_THRESHOLD else 0
store.save_ml_sample(symbol, features, label, actual_change=None)

# Proposed fix — deferred labeling with forward price check:
# Step 1: Save sample with label=None and record entry price
store.save_ml_sample_pending(symbol, features, entry_price=current_price, 
                              timestamp=now_ms)

# Step 2: In a background job 24h later, verify and label:
def label_pending_samples(store):
    pending = store.get_pending_ml_samples(older_than_hours=24)
    for sample in pending:
        symbol = sample['symbol']
        entry_price = sample['entry_price']
        current_price = get_current_price(symbol)
        actual_change = (current_price - entry_price) / entry_price * 100
        label = 1 if actual_change >= 20.0 else 0  # matches PumpDefinition
        store.update_ml_sample_label(sample['id'], label, actual_change)
```

**Schema addition needed:**

```sql
ALTER TABLE ml_training_samples ADD COLUMN entry_price REAL;
ALTER TABLE ml_training_samples ADD COLUMN label_verified INTEGER DEFAULT 0;
```

**Expected impact:** Eliminates AUC inflation from label leakage. Real AUC will likely drop to 0.65–0.80, which is honest and improvable.

---

### Proposal 4: Add Large-Cap Filter / Market-Cap Feature

**Problem:** LTCUSDT and DOGEUSDT scored 75 (highest alerts) because their spread is tight (market makers) and volume patterns look like accumulation. These are multi-billion dollar assets that cannot realistically pump 30% — but the model has no concept of market cap.

**Add market cap tier feature:**

```python
# In analysis/ml_predictor.py FEATURE_NAMES, add:
"log_market_cap_tier",  # log10(approx market cap) or 0 if unknown

# Approximate from volume_24h as proxy (highly correlated):
# Large cap: vol_24h > $500M → tier 3
# Mid cap: $50M–$500M → tier 2  
# Small cap: <$50M → tier 1
def estimate_cap_tier(volume_24h_usd: float) -> float:
    if volume_24h_usd > 500_000_000:
        return 3.0   # large cap — pump unlikely
    elif volume_24h_usd > 50_000_000:
        return 2.0   # mid cap
    else:
        return 1.0   # small cap — pump possible

# In extract_features():
cap_tier = estimate_cap_tier(ind.volume_24h_usd if hasattr(ind, 'volume_24h_usd') else wa.volume_24h)
```

**Also add to `model_params.py` — per-tier score penalty:**

```python
# In ModelParams:
large_cap_score_penalty: int = 20  # subtract from control_score for large caps
# Apply in WhaleDetector._calculate_control_score():
if volume_24h > 500_000_000:
    result.control_score = max(0, result.control_score - MODEL_PARAMS.large_cap_score_penalty)
```

**Expected impact:** Eliminates LTC/DOGE false high-score alerts. Focuses detection on small/mid-cap tokens where manipulation is feasible.

---

### Proposal 5: Add Open Interest Change Feature (OI Velocity)

**Problem:** Open Interest (OI) surge is a strong leading indicator for pumps — when OI increases rapidly while price consolidates, it signals leveraged long accumulation before a squeeze. Currently not tracked at all.

**Add OI tracking to `analysis/indicators.py`:**

```python
# Add to IndicatorResult dataclass:
oi_change_1h: float = 0.0    # OI % change in last 1h
oi_change_4h: float = 0.0    # OI % change in last 4h
oi_price_divergence: float = 0.0  # OI rising while price flat = squeeze setup

# Fetch from Binance futures:
# GET /fapi/v1/openInterestHist?symbol=BTCUSDT&period=1h&limit=5
async def fetch_oi_history(symbol: str) -> dict:
    url = f"https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": "1h", "limit": 5}
    # ... standard async fetch
    data = await fetch(url, params)
    if len(data) >= 2:
        oi_now = float(data[-1]['sumOpenInterest'])
        oi_1h_ago = float(data[-2]['sumOpenInterest'])
        oi_change_1h = (oi_now - oi_1h_ago) / oi_1h_ago if oi_1h_ago > 0 else 0
        return {"oi_change_1h": oi_change_1h}
    return {}
```

**Add to FEATURE_NAMES:**

```python
"oi_change_1h",       # OI 1h velocity
"oi_change_4h",       # OI 4h velocity  
"oi_price_divergence", # OI up + price flat = squeeze setup (oi_change_1h - price_change_1h)
```

**Expected impact:** OI velocity is one of the strongest quantitative signals for squeeze-driven pumps. Many altcoin pumps are short squeezes — currently invisible to the model.

---

## 6. Priority Ranking

| Priority | Proposal | Effort | Expected Impact |
|---|---|---|---|
| P0 (Critical) | #3 Fix label generation | Medium | Fixes data integrity; model currently trains on circular labels |
| P1 (High) | #1 Add dim_funding_rate | Low | One-line feature addition; closes known gap |
| P1 (High) | #4 Large-cap filter | Low | Eliminates LTC/DOGE false alerts immediately |
| P2 (Medium) | #2 Time-of-day features | Low | Adds session context; 3 new features |
| P2 (Medium) | #5 Open Interest | High | New data source needed; high signal value |
