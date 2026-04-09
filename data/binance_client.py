"""
Binance 数据采集客户端
- REST API: K线 / 深度 / 最近成交
- WebSocket: 实时推送 (可选)
"""
import time
import hmac
import hashlib
import json
import os
import logging
from typing import Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import asyncio

from config import BINANCE_API_KEY, BINANCE_API_SECRET, KLINE_INTERVAL, KLINE_LOOKBACK_DAYS, ORDERBOOK_DEPTH

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision"
FAPI_URL = "https://fapi.binance.com"  # 合约 API (仅代理可用)
OKX_URL = "https://www.okx.com"        # OKX 备用 (资金费率等)


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
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"X-MBX-APIKEY": BINANCE_API_KEY} if BINANCE_API_KEY else {},
            proxy=proxy,
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
        """获取永续合约资金费率 (正=多头付费, 做多拥挤)
        优先 OKX, 备选 Binance fapi (需代理)
        """
        # OKX: BTCUSDT → BTC-USDT-SWAP
        okx_inst = symbol.replace("USDT", "-USDT-SWAP")
        async with self._rate_limiter:
            try:
                resp = await self.client.get(
                    f"{OKX_URL}/api/v5/public/funding-rate",
                    params={"instId": okx_inst},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("data"):
                    return float(data["data"][0]["fundingRate"])
            except Exception:
                pass
        # Fallback: Binance fapi
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
                pass
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


# ── WebSocket 实时数据流 ──────────────────────────────────────────────────────

WS_BASE_URL = "wss://stream.binance.com:9443"
MAX_STREAMS_PER_CONNECTION = 200  # 保守限制, Binance 上限 1024


class BinanceWebSocket:
    """
    Binance 合并流 WebSocket 客户端.

    用法:
        ws = BinanceWebSocket(on_message=handler)
        await ws.subscribe(["BTCUSDT", "ETHUSDT"])
        await ws.start()
        ...
        await ws.stop()
    """

    def __init__(self, on_message: Optional[Callable[[str, dict], None]] = None):
        """
        :param on_message: 回调函数 (stream_name: str, data: dict)
        """
        self._on_message = on_message
        self._subscribed: set = set()       # 已订阅 symbol 集合
        self._running = False
        self._ws = None
        self._recv_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 1.0         # 初始重连延迟(秒)
        self._max_reconnect_delay = 60.0

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    async def start(self):
        """启动 WebSocket 连接 (含自动重连)"""
        self._running = True
        self._recv_task = asyncio.create_task(self._run_forever())
        logger.info("[WS] BinanceWebSocket 启动")

    async def stop(self):
        """优雅关闭"""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        logger.info("[WS] BinanceWebSocket 已停止")

    async def subscribe(self, symbols: List[str]):
        """动态订阅新代币 (如连接已建立则发送 SUBSCRIBE 指令)"""
        new_syms = [s for s in symbols if s not in self._subscribed]
        if not new_syms:
            return
        self._subscribed.update(new_syms)
        if self._ws is not None:
            streams = self._build_streams(new_syms)
            await self._send_subscribe(streams)
            logger.info(f"[WS] 订阅 {new_syms} -> {len(streams)} 流")

    async def unsubscribe(self, symbols: List[str]):
        """取消订阅代币"""
        rem_syms = [s for s in symbols if s in self._subscribed]
        if not rem_syms:
            return
        for s in rem_syms:
            self._subscribed.discard(s)
        if self._ws is not None:
            streams = self._build_streams(rem_syms)
            await self._send_unsubscribe(streams)
            logger.info(f"[WS] 取消订阅 {rem_syms}")

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _build_streams(self, symbols: List[str]) -> List[str]:
        """构建流名列表"""
        streams = []
        for sym in symbols:
            s = sym.lower()
            streams.append(f"{s}@aggTrade")
            streams.append(f"{s}@kline_1m")
            streams.append(f"{s}@depth20@100ms")
        return streams

    def _build_url(self) -> str:
        """构建合并流 URL (最多 MAX_STREAMS_PER_CONNECTION 个流)"""
        all_streams = self._build_streams(list(self._subscribed))
        # 按批次分组, 本实现只维护单连接; 超大量代币时截断到上限
        streams = all_streams[:MAX_STREAMS_PER_CONNECTION * 3]
        if streams:
            return f"{WS_BASE_URL}/stream?streams={'/'.join(streams)}"
        return f"{WS_BASE_URL}/ws"

    async def _run_forever(self):
        """带指数退避的永久重连循环 (451=地理封锁时停止重试)"""
        while self._running:
            try:
                await self._connect_and_receive()
                self._reconnect_delay = 1.0  # 正常断开后重置延迟
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e)
                if "451" in err_str or "geo" in err_str.lower():
                    logger.warning(f"[WS] 地理封锁 (451), 停止重连 — 仅使用轮询模式")
                    break
                logger.warning(f"[WS] 连接异常: {e}, {self._reconnect_delay:.0f}s 后重连")
            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _connect_and_receive(self):
        """建立单次 WebSocket 连接并持续接收消息"""
        try:
            import websockets
        except ImportError:
            logger.error("[WS] 缺少 websockets 库, 请执行: pip install websockets>=12.0")
            self._running = False
            return

        if not self._subscribed:
            logger.debug("[WS] 无订阅代币, 等待中...")
            await asyncio.sleep(5)
            return

        url = self._build_url()
        logger.info(f"[WS] 连接: {url[:80]}...")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # 连接成功后重置
            logger.info("[WS] 已连接")
            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    stream = msg.get("stream", "")
                    data = msg.get("data", msg)
                    if self._on_message and stream:
                        self._on_message(stream, data)
                except Exception as e:
                    logger.debug(f"[WS] 消息解析错误: {e}")

        self._ws = None

    async def _send_subscribe(self, streams: List[str]):
        """向已建立的连接发送 SUBSCRIBE 指令"""
        if self._ws is None:
            return
        # 按批次发送, 每批最多 200 流
        for i in range(0, len(streams), MAX_STREAMS_PER_CONNECTION):
            batch = streams[i:i + MAX_STREAMS_PER_CONNECTION]
            payload = json.dumps({"method": "SUBSCRIBE", "params": batch, "id": int(time.time())})
            try:
                await self._ws.send(payload)
            except Exception as e:
                logger.warning(f"[WS] 订阅发送失败: {e}")

    async def _send_unsubscribe(self, streams: List[str]):
        """向已建立的连接发送 UNSUBSCRIBE 指令"""
        if self._ws is None:
            return
        payload = json.dumps({"method": "UNSUBSCRIBE", "params": streams, "id": int(time.time())})
        try:
            await self._ws.send(payload)
        except Exception as e:
            logger.warning(f"[WS] 取消订阅发送失败: {e}")
