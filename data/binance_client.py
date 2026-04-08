"""
Binance 数据采集客户端
- REST API: K线 / 深度 / 最近成交
- WebSocket: 实时推送 (可选)
"""
import time
import hmac
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import asyncio

from config import BINANCE_API_KEY, BINANCE_API_SECRET, KLINE_INTERVAL, KLINE_LOOKBACK_DAYS, ORDERBOOK_DEPTH

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"
FAPI_URL = "https://fapi.binance.com"  # 合约 API (用于资金费率等)


@dataclass
class KlineBar:
    """单根K线"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float          # 成交量 (base)
    quote_volume: float    # 成交额 (USDT)
    trades: int            # 成交笔数
    taker_buy_volume: float   # 主动买入量
    taker_buy_quote: float    # 主动买入额


@dataclass
class OrderBookSnapshot:
    """深度快照"""
    timestamp: int
    bids: List[Tuple[float, float]]   # [(price, qty), ...]
    asks: List[Tuple[float, float]]
    bid_total: float        # 买盘总量
    ask_total: float        # 卖盘总量
    spread_pct: float       # 买卖价差百分比
    bid_ask_ratio: float    # 买卖比


@dataclass
class TradeStats:
    """最近成交统计"""
    timestamp: int
    total_trades: int
    large_trade_count: int     # 大单笔数
    large_trade_pct: float     # 大单成交占比
    avg_trade_size: float      # 平均单笔成交额
    max_trade_size: float      # 最大单笔
    buy_ratio: float           # 主动买入比例


class BinanceClient:
    """Binance 数据采集"""

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"X-MBX-APIKEY": BINANCE_API_KEY} if BINANCE_API_KEY else {},
        )
        self._rate_limiter = asyncio.Semaphore(8)  # 并发限制

    async def close(self):
        await self.client.aclose()

    # ── K线数据 ─────────────────────────────────────────────────────────

    async def get_klines(
        self, symbol: str, interval: str = KLINE_INTERVAL,
        days: int = KLINE_LOOKBACK_DAYS,
    ) -> List[KlineBar]:
        """获取历史K线"""
        end_time = int(time.time() * 1000)
        start_time = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        all_bars = []
        limit = 1000

        while start_time < end_time:
            async with self._rate_limiter:
                try:
                    resp = await self.client.get(f"{BASE_URL}/api/v3/klines", params={
                        "symbol": symbol,
                        "interval": interval,
                        "startTime": start_time,
                        "endTime": end_time,
                        "limit": limit,
                    })
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.error(f"[Binance] K线获取失败 {symbol}: {e}")
                    break

                if not data:
                    break

                for bar in data:
                    all_bars.append(KlineBar(
                        timestamp=bar[0],
                        open=float(bar[1]),
                        high=float(bar[2]),
                        low=float(bar[3]),
                        close=float(bar[4]),
                        volume=float(bar[5]),
                        quote_volume=float(bar[7]),
                        trades=int(bar[8]),
                        taker_buy_volume=float(bar[9]),
                        taker_buy_quote=float(bar[10]),
                    ))

                start_time = data[-1][0] + 1
                await asyncio.sleep(0.1)  # rate limit

        logger.info(f"[Binance] {symbol} 获取 {len(all_bars)} 根K线")
        return all_bars

    # ── 深度数据 ─────────────────────────────────────────────────────────

    async def get_orderbook(self, symbol: str, limit: int = ORDERBOOK_DEPTH) -> Optional[OrderBookSnapshot]:
        """获取深度快照"""
        async with self._rate_limiter:
            try:
                resp = await self.client.get(f"{BASE_URL}/api/v3/depth", params={
                    "symbol": symbol,
                    "limit": limit,
                })
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"[Binance] 深度获取失败 {symbol}: {e}")
                return None

        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]

        if not bids or not asks:
            return None

        bid_total = sum(p * q for p, q in bids)
        ask_total = sum(p * q for p, q in asks)
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread_pct = (best_ask - best_bid) / best_bid * 100 if best_bid > 0 else 0
        ratio = bid_total / (bid_total + ask_total) if (bid_total + ask_total) > 0 else 0.5

        return OrderBookSnapshot(
            timestamp=int(time.time() * 1000),
            bids=bids, asks=asks,
            bid_total=bid_total, ask_total=ask_total,
            spread_pct=spread_pct,
            bid_ask_ratio=ratio,
        )

    # ── 最近成交 ─────────────────────────────────────────────────────────

    async def get_recent_trades(
        self, symbol: str, limit: int = 1000,
        large_threshold_usdt: float = 50000,
    ) -> Optional[TradeStats]:
        """获取最近成交, 分析大单占比"""
        async with self._rate_limiter:
            try:
                resp = await self.client.get(f"{BASE_URL}/api/v3/trades", params={
                    "symbol": symbol,
                    "limit": limit,
                })
                resp.raise_for_status()
                trades = resp.json()
            except Exception as e:
                logger.error(f"[Binance] 成交获取失败 {symbol}: {e}")
                return None

        if not trades:
            return None

        total = len(trades)
        trade_sizes = []
        large_count = 0
        large_volume = 0
        total_volume = 0
        buy_count = 0

        for t in trades:
            price = float(t["price"])
            qty = float(t["qty"])
            size_usdt = price * qty
            trade_sizes.append(size_usdt)
            total_volume += size_usdt

            if size_usdt >= large_threshold_usdt:
                large_count += 1
                large_volume += size_usdt

            if not t.get("isBuyerMaker", False):
                buy_count += 1  # isBuyerMaker=False → 主动买入

        return TradeStats(
            timestamp=int(time.time() * 1000),
            total_trades=total,
            large_trade_count=large_count,
            large_trade_pct=(large_volume / total_volume * 100) if total_volume > 0 else 0,
            avg_trade_size=total_volume / total if total > 0 else 0,
            max_trade_size=max(trade_sizes) if trade_sizes else 0,
            buy_ratio=buy_count / total if total > 0 else 0.5,
        )

    # ── 24h Ticker ───────────────────────────────────────────────────────

    async def get_ticker_24h(self, symbol: str) -> Optional[Dict]:
        """24小时行情摘要"""
        async with self._rate_limiter:
            try:
                resp = await self.client.get(f"{BASE_URL}/api/v3/ticker/24hr", params={"symbol": symbol})
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.error(f"[Binance] Ticker获取失败 {symbol}: {e}")
                return None

    # ── 合约资金费率 (辅助判断) ──────────────────────────────────────────

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """获取永续合约资金费率 (正=多头付费, 做多拥挤)"""
        async with self._rate_limiter:
            try:
                resp = await self.client.get(f"{FAPI_URL}/fapi/v1/fundingRate", params={
                    "symbol": symbol, "limit": 1,
                })
                resp.raise_for_status()
                data = resp.json()
                if data:
                    return float(data[0]["fundingRate"])
            except Exception:
                pass  # 部分代币没有合约
        return None

    # ── 批量采集 ─────────────────────────────────────────────────────────

    async def collect_full_snapshot(self, symbol: str) -> Dict:
        """一次性采集某代币的全部数据"""
        klines, orderbook, trades, ticker = await asyncio.gather(
            self.get_klines(symbol),
            self.get_orderbook(symbol),
            self.get_recent_trades(symbol),
            self.get_ticker_24h(symbol),
            return_exceptions=True,
        )

        # 容错处理
        if isinstance(klines, Exception):
            logger.error(f"[collect] {symbol} klines error: {klines}")
            klines = []
        if isinstance(orderbook, Exception):
            orderbook = None
        if isinstance(trades, Exception):
            trades = None
        if isinstance(ticker, Exception):
            ticker = None

        funding = await self.get_funding_rate(symbol)

        return {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "klines": klines,
            "orderbook": orderbook,
            "trades": trades,
            "ticker": ticker,
            "funding_rate": funding,
        }

    async def collect_all_tokens(self, symbols: List[str]) -> Dict[str, Dict]:
        """批量采集所有代币数据"""
        results = {}
        # 分批, 每批 4 个, 避免触发频率限制
        batch_size = 4
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            tasks = [self.collect_full_snapshot(s) for s in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for sym, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.error(f"[collect] {sym} failed: {result}")
                    continue
                results[sym] = result
            if i + batch_size < len(symbols):
                await asyncio.sleep(1)  # 批间暂停
        return results
