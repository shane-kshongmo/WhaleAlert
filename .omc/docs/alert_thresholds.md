# Alert Thresholds Configuration

## Trading Performance Alerts

### Sample Size Requirements
**DO NOT trigger threshold alerts before reaching these minimums:**
- Minimum: **25 trades per tier** (halfway to validation target)
- Full validation: **50 trades per tier**

### Alert Thresholds (After 25 trades/tier)

```python
ALERT_THRESHOLDS = {
    'min_trades': 25,  # Minimum trades before alerts active
    
    # Win Rate Alerts
    'win_rate_warning': 0.45,    # 45% - Investigate
    'win_rate_critical': 0.40,   # 40% - Immediate action
    
    # Drawdown Alerts (more stable than win rate)
    'drawdown_warning': -0.10,   # -10% - Monitor closely
    'drawdown_critical': -0.15,   # -15% - Stop trading, investigate
    
    # Streak Alerts (regardless of sample size)
    'consecutive_losses': 4,       # 4 losses in a row
    'consecutive_wins': 8,        # 8 wins in a row (check overfitting)
    
    # Single Loss Alerts (regardless of sample size)
    'max_single_loss_usd': 100,   # $100 loss
    'max_single_loss_pct': 0.10,  # 10% loss
}
```

## System Health Alerts (Anytime)

```python
SYSTEM_ALERTS = {
    # Service stopped
    'no_scans_hours': 2,          # No scans for 2 hours
    
    # ML data collection stopped
    'pending_ml_stagnant_hours': 2,  # Pending samples not growing
    
    # Bug detection
    'zero_pnl_eviction': True,    # Trade closed with $0 PnL
    'gap_protection_breach': True, # Loss >12% overnight
}
```

## Priority Levels

### 🔴 CRITICAL (Immediate Action Required)
- Drawdown exceeds -15%
- 4+ consecutive losses
- Service stopped scanning
- Zero PnL eviction (bug indicator)

### 🟡 WARNING (Monitor Closely)
- Win rate drops below 45%
- Drawdown exceeds -10%
- Large single loss (> $100 or > -10%)

### 🔵 INFO (Log Only)
- Win rate changes by ±10%
- Every 10 trades closed
- Weekly summary

## Implementation Status

**Current:**
- ✅ Weekly reviews (Mondays 9am)
- ✅ Manual reviews on-demand
- ❌ Real-time threshold alerts: NOT IMPLEMENTED

**Recommended Next Steps:**
1. Wait for 25 trades/tier before enabling win rate alerts
2. Implement streak alerts NOW (4+ losses)
3. Implement large loss alerts NOW (>$100 or >-10%)
4. Add drawdown tracking after 25 trades/tier

## When to Enable Each Alert Type

| Alert Type | Now | After 25 Trades/Tier | After 50 Trades/Tier |
|------------|-----|----------------------|----------------------|
| Streak alerts | ✅ YES | ✅ YES | ✅ YES |
| Large loss alerts | ✅ YES | ✅ YES | ✅ YES |
| System health | ✅ YES | ✅ YES | ✅ YES |
| Win rate alerts | ❌ NO | ✅ YES | ✅ YES |
| Drawdown alerts | ❌ NO | ❌ NO | ✅ YES |
| Sharpe ratio | ❌ NO | ❌ NO | ✅ YES |

## Sharpe Ratio Consideration

**Why NOT to use Sharpe ratio yet:**
- Requires at least 20-30 trades with consistent volatility
- With 13 trades, standard deviation is meaningless
- Can be gamed by one big win

**When to add:**
- After 50 trades/tier validated
- Calculate using: `(Return - RiskFreeRate) / StdDev`
- Threshold: Alert if Sharpe < 0.5 (below 0.5 = bad risk-adjusted returns)
