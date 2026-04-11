# P0-3 Pump Detection Investigation Report

**Date:** 2026-04-11
**Issue:** pump_events table has 0 rows despite system running
**Status:** 🔴 ROOT CAUSE IDENTIFIED - BUG FOUND

---

## Root Cause

**File:** `data/auto_discovery.py` line 455

```python
async def get_all_price_changes(self) -> Dict[str, Dict]:
    """获取所有监控代币的价格变化 (用于精确的爆涨检测)"""
    tickers = await self._fetch_all_tickers()
    result = {}
    for t in WATCH_TOKENS:
        ticker = tickers.get(t.symbol)
        if not ticker:
            continue
        result[t.symbol] = {
            "price": ticker.price,
            "change_24h": ticker.change_pct,
            "change_1h": 0,    # ticker 不提供, 需要 K线
            "change_4h": 0,
            "volume_current": ticker.quote_volume,
            "volume_avg": ticker.quote_volume,  # ❌ BUG: Should be historical avg!
            "trades_24h": ticker.trades,
            "price_range_pct": ticker.price_range_pct,
        }
    return result
```

**The Bug:** `volume_avg` is set to `ticker.quote_volume` (current volume) instead of historical average.

**Impact:**
- In `pump_monitor.py` line 206: `vol_ratio = vol_current / vol_avg`
- Since `vol_avg = vol_current`, the ratio is always **1.0**
- Pump requirement: `vol_ratio >= 1.5` (line 210)
- **Result:** NO pumps can ever be detected, even if price gain ≥20%

---

## Evidence

**Database Check:**
```
pump_events rows: 0
Snapshots with change_24h >= 20%: 0
Max 24h gain seen: +16.0% (NOMUSDT)
```

**Even if a token gains 20%+, it won't be detected because:**
```
change_24h = 25.0%  ✅ Meets ≥20% requirement
vol_current = 1,000,000
vol_avg = 1,000,000  ❌ Should be ~500,000 (historical avg)
vol_ratio = 1.0 / 1.0 = 1.0  ❌ Fails ≥1.5x requirement
Result: NOT recorded as pump
```

---

## Required Fix

### Option 1: Quick Fix (Use volume_24h from snapshots)

**File:** `data/auto_discovery.py`

```python
async def get_all_price_changes(self) -> Dict[str, Dict]:
    """获取所有监控代币的价格变化 (用于精确的爆涨检测)"""
    tickers = await self._fetch_all_tickers()
    result = {}

    # Get historical average volumes from snapshots
    with self.store._conn() as conn:
        avg_volumes = dict(conn.execute("""
            SELECT symbol, AVG(volume_24h) as avg_vol
            FROM snapshots
            WHERE timestamp > ?
            GROUP BY symbol
        """, (int((time.time() - 7*24*3600) * 1000),)).fetchall())

    for t in WATCH_TOKENS:
        ticker = tickers.get(t.symbol)
        if not ticker:
            continue

        # Use 7-day average from snapshots, fallback to current volume
        avg_vol = avg_volumes.get(t.symbol, ticker.quote_volume)

        result[t.symbol] = {
            "price": ticker.price,
            "change_24h": ticker.change_pct,
            "change_1h": 0,
            "change_4h": 0,
            "volume_current": ticker.quote_volume,
            "volume_avg": avg_vol,  # ✅ FIXED: Use historical average
            "trades_24h": ticker.trades,
            "price_range_pct": ticker.price_range_pct,
        }
    return result
```

### Option 2: Robust Fix (Query Binance klines for hourly data)

**File:** `data/auto_discovery.py`

```python
async def get_all_price_changes(self) -> Dict[str, Dict]:
    """获取所有监控代币的价格变化 (用于精确的爆涨检测)"""
    tickers = await self._fetch_all_tickers()
    result = {}

    for t in WATCH_TOKENS:
        ticker = tickers.get(t.symbol)
        if not ticker:
            continue

        # Fetch 24h klines to calculate average volume
        klines = await self.binance.client.get_klines(
            symbol=t.symbol,
            interval=KLINE_INTERVAL_1HOUR,
            limit=24
        )

        # Calculate 24h average volume (sum of last 24 hours / 24)
        vol_24h_sum = sum(float(k[5]) for k in klines[-24:])  # volume is at index 5
        vol_avg = vol_24h_sum / 24 if klines else ticker.quote_volume

        # Calculate 1h, 4h, 24h price changes from klines
        close_1h_ago = float(klines[-2][4]) if len(klines) >= 2 else ticker.price
        close_4h_ago = float(klines[-5][4]) if len(klines) >= 5 else ticker.price
        close_24h_ago = float(klines[-24][4]) if len(klines) >= 24 else ticker.price

        change_1h = ((ticker.price - close_1h_ago) / close_1h_ago * 100) if close_1h_ago > 0 else 0
        change_4h = ((ticker.price - close_4h_ago) / close_4h_ago * 100) if close_4h_ago > 0 else 0
        change_24h = ((ticker.price - close_24h_ago) / close_24h_ago * 100) if close_24h_ago > 0 else ticker.change_pct

        result[t.symbol] = {
            "price": ticker.price,
            "change_24h": change_24h,
            "change_1h": change_1h,  # ✅ Now populated from klines
            "change_4h": change_4h,  # ✅ Now populated from klines
            "volume_current": ticker.quote_volume,
            "volume_avg": vol_avg,  # ✅ FIXED: 24h average from klines
            "trades_24h": ticker.trades,
            "price_range_pct": ticker.price_range_pct,
        }
    return result
```

---

## Recommendation

**Use Option 1 (Quick Fix) first:**
- Simpler implementation
- Uses existing snapshot data
- No additional API calls
- Can be deployed immediately

**Then implement Option 2 (Robust Fix):**
- More accurate volume surge detection
- Populates change_1h and change_4h (currently always 0)
- Better for fast-moving pumps (<24h)

---

## Success Criteria

After fix:
- [ ] `get_all_price_changes()` returns correct `volume_avg` (different from `volume_current`)
- [ ] Tokens with `change_24h >= 20%` AND `vol_current/vol_avg >= 1.5` are detected
- [ ] `pump_events` table starts getting rows
- [ ] `was_pumped_7d` feature is no longer constant 30.0
- [ ] `days_since_last_pump` feature is no longer all-zero

---

## Testing

**To verify the fix works:**
1. Deploy fix to production
2. Wait for next scan cycle (15 minutes)
3. Query database:
   ```sql
   SELECT COUNT(*) FROM pump_events;
   ```
4. If fix works, count should increase when pumps occur

**To force a test:**
- Temporarily lower `PUMP_DEF.min_pump_pct_24h` from 20.0 to 10.0
- This should trigger pump detection for tokens like NOMUSDT (+16.0%)
- After verification, restore to 20.0

---

**Report Status:** ✅ COMPLETE - Root cause identified, fix documented
**Next Step:** Implement Option 1 fix in `data/auto_discovery.py`
