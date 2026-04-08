"""
技术指标计算模块
从 K线数据中提取用于控盘分析的关键指标
"""
import math
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class IndicatorResult:
    """某代币的全部技术指标"""
    symbol: str

    # 价格相关
    price: float = 0
    change_24h: float = 0
    change_7d: float = 0
    change_30d: float = 0

    # 成交量分析
    vol_current: float = 0        # 最近 7 日日均成交额
    vol_prev: float = 0           # 前 30 日日均成交额
    vol_shrink_ratio: float = 1.0 # 缩量比 (越小越缩量)
    vol_spike_ratio: float = 1.0  # 最近一日成交量/均值

    # 价格波动
    price_range_30d: float = 0    # 30日振幅
    price_range_7d: float = 0     # 7日振幅
    atr_pct: float = 0            # ATR 百分比

    # 主动买卖
    taker_buy_ratio_7d: float = 0.5  # 7日主动买入占比
    taker_buy_ratio_24h: float = 0.5 # 24h主动买入占比

    # 成交笔数分析
    avg_trade_size_7d: float = 0     # 7日平均单笔成交额
    avg_trade_size_prev: float = 0   # 前30日平均单笔

    # 趋势
    sma7: float = 0
    sma20: float = 0
    sma60: float = 0
    ema12: float = 0
    ema26: float = 0
    macd: float = 0
    macd_signal: float = 0
    macd_histogram: float = 0
    rsi_14: float = 50

    # 布林带
    bb_upper: float = 0
    bb_lower: float = 0
    bb_mid: float = 0
    bb_width: float = 0           # 布林带宽度 (窄=即将突破)


def calc_indicators(klines: List, symbol: str = "") -> IndicatorResult:
    """
    从 K线数据计算全部指标
    klines: 列表, 每个元素需有 close, volume, quote_volume, taker_buy_volume, trades 属性
    """
    result = IndicatorResult(symbol=symbol)

    if not klines or len(klines) < 30:
        return result

    closes = [k.close if hasattr(k, 'close') else k['close'] for k in klines]
    volumes = [k.quote_volume if hasattr(k, 'quote_volume') else k.get('quote_volume', 0) for k in klines]
    taker_buys = [k.taker_buy_quote if hasattr(k, 'taker_buy_quote') else k.get('taker_buy_volume', 0) for k in klines]
    trade_counts = [k.trades if hasattr(k, 'trades') else k.get('trades', 1) for k in klines]
    highs = [k.high if hasattr(k, 'high') else k['high'] for k in klines]
    lows = [k.low if hasattr(k, 'low') else k['low'] for k in klines]

    n = len(closes)

    # ── 价格 ──
    result.price = closes[-1]
    if n > 1 and closes[-2] > 0:
        result.change_24h = (closes[-1] - closes[-2]) / closes[-2] * 100
    if n > 7 and closes[-8] > 0:
        result.change_7d = (closes[-1] - closes[-8]) / closes[-8] * 100
    if n > 30 and closes[-31] > 0:
        result.change_30d = (closes[-1] - closes[-31]) / closes[-31] * 100

    # ── 成交量分析 ──
    recent7 = volumes[-7:]
    prev30 = volumes[-37:-7] if n >= 37 else volumes[:max(1, n - 7)]
    result.vol_current = sum(recent7) / len(recent7) if recent7 else 0
    result.vol_prev = sum(prev30) / len(prev30) if prev30 else 1
    result.vol_shrink_ratio = result.vol_current / result.vol_prev if result.vol_prev > 0 else 1
    result.vol_spike_ratio = volumes[-1] / result.vol_current if result.vol_current > 0 else 1

    # ── 价格波动 ──
    recent30_close = closes[-30:]
    recent7_close = closes[-7:]
    if recent30_close:
        hi = max(recent30_close)
        lo = min(recent30_close)
        result.price_range_30d = (hi - lo) / lo if lo > 0 else 0
    if recent7_close:
        hi = max(recent7_close)
        lo = min(recent7_close)
        result.price_range_7d = (hi - lo) / lo if lo > 0 else 0

    # ATR (14)
    atr_sum = 0
    atr_period = min(14, n - 1)
    for i in range(n - atr_period, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]) if i > 0 else 0,
            abs(lows[i] - closes[i - 1]) if i > 0 else 0,
        )
        atr_sum += tr
    atr = atr_sum / atr_period if atr_period > 0 else 0
    result.atr_pct = atr / closes[-1] * 100 if closes[-1] > 0 else 0

    # ── 主动买卖比 ──
    if sum(recent7) > 0:
        result.taker_buy_ratio_7d = sum(taker_buys[-7:]) / sum(recent7)
    if volumes[-1] > 0:
        result.taker_buy_ratio_24h = taker_buys[-1] / volumes[-1]

    # ── 平均单笔 ──
    recent7_trades = trade_counts[-7:]
    prev30_trades = trade_counts[-37:-7] if n >= 37 else trade_counts[:max(1, n - 7)]
    if sum(recent7_trades) > 0:
        result.avg_trade_size_7d = sum(recent7) / sum(recent7_trades)
    if sum(prev30_trades) > 0:
        prev30_vol = volumes[-37:-7] if n >= 37 else volumes[:max(1, n - 7)]
        result.avg_trade_size_prev = sum(prev30_vol) / sum(prev30_trades)

    # ── SMA ──
    result.sma7 = sum(closes[-7:]) / 7 if n >= 7 else closes[-1]
    result.sma20 = sum(closes[-20:]) / 20 if n >= 20 else closes[-1]
    result.sma60 = sum(closes[-60:]) / 60 if n >= 60 else closes[-1]

    # ── EMA + MACD ──
    ema12 = closes[0]
    ema26 = closes[0]
    signal = 0
    for c in closes:
        ema12 = c * (2 / 13) + ema12 * (1 - 2 / 13)
        ema26 = c * (2 / 27) + ema26 * (1 - 2 / 27)
        macd_val = ema12 - ema26
        signal = macd_val * (2 / 10) + signal * (1 - 2 / 10)
    result.ema12 = ema12
    result.ema26 = ema26
    result.macd = ema12 - ema26
    result.macd_signal = signal
    result.macd_histogram = result.macd - signal

    # ── RSI (14) ──
    period = 14
    gains = 0
    losses = 0
    for i in range(1, min(period + 1, n)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    if period <= n:
        gains /= period
        losses /= period
        for i in range(period + 1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains = (gains * (period - 1) + diff) / period
                losses = (losses * (period - 1)) / period
            else:
                gains = (gains * (period - 1)) / period
                losses = (losses * (period - 1) - diff) / period
    rs = gains / losses if losses > 0 else 100
    result.rsi_14 = 100 - 100 / (1 + rs)

    # ── Bollinger Bands (20, 2) ──
    bb_period = 20
    if n >= bb_period:
        bb_slice = closes[-bb_period:]
        bb_mean = sum(bb_slice) / bb_period
        bb_std = math.sqrt(sum((c - bb_mean) ** 2 for c in bb_slice) / bb_period)
        result.bb_upper = bb_mean + 2 * bb_std
        result.bb_lower = bb_mean - 2 * bb_std
        result.bb_mid = bb_mean
        result.bb_width = (result.bb_upper - result.bb_lower) / result.bb_mid * 100 if result.bb_mid > 0 else 0

    return result
