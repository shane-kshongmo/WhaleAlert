"""
实时引擎 — 聚合 WebSocket 原始流数据为可用指标

数据流:
  BinanceWebSocket (aggTrade / kline_1m / depth20) → RealtimeEngine → metrics / fast_signals
"""
import logging
import time
from collections import deque
from typing import Callable, Dict, List, Optional

from data.binance_client import BinanceWebSocket

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────────────

LARGE_TRADE_USDT = 50_000       # 大单阈值
VOLUME_SURGE_FAST_SIGNAL = 3.0  # 5m 量倍阈值触发快速信号
PRICE_MOVE_FAST_SIGNAL = 5.0    # 15m 价格变动阈值 (%)
LARGE_TRADE_CLUSTER = 5         # 1 分钟内大单数量阈值
PRICE_TICKS_MAX = 100           # 保留最近价格 tick 数
AVG_VOLUME_WINDOW = 60          # 计算均量的样本数


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class _TradeTick:
    """单笔成交记录"""
    __slots__ = ("ts", "price", "usdt", "is_buy")

    def __init__(self, ts: float, price: float, usdt: float, is_buy: bool):
        self.ts = ts
        self.price = price
        self.usdt = usdt
        self.is_buy = is_buy


class _DepthState:
    """最新深度快照"""
    __slots__ = ("bid_total", "ask_total")

    def __init__(self):
        self.bid_total = 0.0
        self.ask_total = 0.0


class _SymbolState:
    """单代币实时状态"""

    def __init__(self):
        # 原始成交 tick (无限期保留最近 15 分钟)
        self.trades: deque = deque()  # deque of _TradeTick

        # 价格 tick (最近 PRICE_TICKS_MAX 个)
        self.price_ticks: deque = deque(maxlen=PRICE_TICKS_MAX)  # (ts, price)

        # 每分钟聚合量 (最近 AVG_VOLUME_WINDOW 分钟, 用于均量计算)
        self.volume_1m_samples: deque = deque(maxlen=AVG_VOLUME_WINDOW)

        # 深度
        self.depth = _DepthState()

        # 当前 1m bucket 的临时状态
        self._bucket_start: float = 0.0
        self._bucket_volume: float = 0.0


# ── 主类 ─────────────────────────────────────────────────────────────────────

class RealtimeEngine:
    """
    包装 BinanceWebSocket, 聚合原始流数据为实时指标.

    使用方式:
        engine = RealtimeEngine()
        await engine.start(symbols=["BTCUSDT", "PEPEUSDT"])
        metrics = engine.get_realtime_metrics("BTCUSDT")
        signals = engine.get_pending_fast_signals()
        await engine.stop()
    """

    def __init__(self):
        self._ws = BinanceWebSocket(on_message=self._on_ws_message)
        self._states: Dict[str, _SymbolState] = {}
        self._fast_signals: List[dict] = []

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def start(self, symbols: Optional[List[str]] = None):
        """启动引擎并订阅代币"""
        if symbols:
            for sym in symbols:
                self._states[sym] = _SymbolState()
            await self._ws.subscribe(symbols)
        await self._ws.start()
        logger.info(f"[RealtimeEngine] 启动, 订阅 {symbols or []} 代币")

    async def stop(self):
        """优雅关闭"""
        await self._ws.stop()
        logger.info("[RealtimeEngine] 已停止")

    async def subscribe(self, symbols: List[str]):
        """动态增加订阅代币"""
        for sym in symbols:
            if sym not in self._states:
                self._states[sym] = _SymbolState()
        await self._ws.subscribe(symbols)

    async def unsubscribe(self, symbols: List[str]):
        """取消订阅代币"""
        await self._ws.unsubscribe(symbols)

    # ── 指标查询 ──────────────────────────────────────────────────────────────

    def get_realtime_metrics(self, symbol: str) -> dict:
        """
        返回代币实时指标字典:
          volume_surge_1m, volume_surge_5m,
          price_change_5m, price_change_15m,
          trade_intensity, large_trade_count_5m,
          bid_ask_imbalance
        """
        st = self._states.get(symbol)
        if st is None:
            return self._empty_metrics()

        now = time.time()
        trades = list(st.trades)  # snapshot

        # ── 量涌 ──
        vol_1m = self._volume_window(trades, now, 60)
        vol_5m = self._volume_window(trades, now, 300)
        avg_1m = (sum(st.volume_1m_samples) / len(st.volume_1m_samples)
                  if st.volume_1m_samples else 1.0)
        # 5m 均量: 取最近 5 个 1m 样本均值
        last5 = list(st.volume_1m_samples)[-5:] if len(st.volume_1m_samples) >= 5 else list(st.volume_1m_samples)
        avg_5m = (sum(last5) / len(last5)) * 5 if last5 else 1.0

        volume_surge_1m = vol_1m / avg_1m if avg_1m > 0 else 1.0
        volume_surge_5m = vol_5m / avg_5m if avg_5m > 0 else 1.0

        # ── 价格变动 ──
        price_change_5m = self._price_change(st.price_ticks, now, 300)
        price_change_15m = self._price_change(st.price_ticks, now, 900)

        # ── 成交强度 ──
        trades_1m = [t for t in trades if now - t.ts <= 60]
        trade_intensity = len(trades_1m) / 60.0  # trades/sec

        # ── 5m 大单数量 ──
        trades_5m = [t for t in trades if now - t.ts <= 300]
        large_trade_count_5m = sum(1 for t in trades_5m if t.usdt >= LARGE_TRADE_USDT)

        # ── 买卖盘不平衡 ──
        d = st.depth
        total = d.bid_total + d.ask_total
        bid_ask_imbalance = d.bid_total / total if total > 0 else 0.5

        return {
            "symbol": symbol,
            "volume_surge_1m": round(volume_surge_1m, 3),
            "volume_surge_5m": round(volume_surge_5m, 3),
            "price_change_5m": round(price_change_5m, 4),
            "price_change_15m": round(price_change_15m, 4),
            "trade_intensity": round(trade_intensity, 4),
            "large_trade_count_5m": large_trade_count_5m,
            "bid_ask_imbalance": round(bid_ask_imbalance, 4),
        }

    def get_pending_fast_signals(self) -> List[dict]:
        """返回并清空待处理快速信号列表"""
        signals = self._fast_signals[:]
        self._fast_signals.clear()
        return signals

    # ── WebSocket 消息处理 ────────────────────────────────────────────────────

    def _on_ws_message(self, stream: str, data: dict):
        """WebSocket 回调 (在 asyncio event loop 线程中调用)"""
        try:
            parts = stream.split("@")
            if len(parts) < 2:
                return
            sym_lower = parts[0]
            stream_type = "@".join(parts[1:])

            # 反查大写 symbol
            symbol = self._find_symbol(sym_lower)
            if symbol is None:
                return

            if stream_type == "aggTrade":
                self._handle_agg_trade(symbol, data)
            elif stream_type.startswith("kline"):
                self._handle_kline(symbol, data)
            elif stream_type.startswith("depth"):
                self._handle_depth(symbol, data)
        except Exception as e:
            logger.debug(f"[RealtimeEngine] 消息处理异常 {stream}: {e}")

    def _handle_agg_trade(self, symbol: str, data: dict):
        st = self._states[symbol]
        price = float(data.get("p", 0))
        qty = float(data.get("q", 0))
        usdt = price * qty
        ts = data.get("T", time.time() * 1000) / 1000.0
        is_buy = not data.get("m", True)  # m=True 表示买方是 maker, 即主动卖

        tick = _TradeTick(ts=ts, price=price, usdt=usdt, is_buy=is_buy)
        st.trades.append(tick)
        st.price_ticks.append((ts, price))

        # 1m bucket 聚合
        now = time.time()
        if st._bucket_start == 0.0:
            st._bucket_start = now
        if now - st._bucket_start >= 60:
            st.volume_1m_samples.append(st._bucket_volume)
            st._bucket_volume = 0.0
            st._bucket_start = now
        st._bucket_volume += usdt

        # 清理 15 分钟前的成交
        cutoff = now - 900
        while st.trades and st.trades[0].ts < cutoff:
            st.trades.popleft()

        # 快速信号检测
        self._detect_fast_signals(symbol, st, now)

    def _handle_kline(self, symbol: str, data: dict):
        k = data.get("k", {})
        price = float(k.get("c", 0))
        if price > 0:
            st = self._states[symbol]
            st.price_ticks.append((time.time(), price))

    def _handle_depth(self, symbol: str, data: dict):
        st = self._states[symbol]
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        bid_total = sum(float(p) * float(q) for p, q in bids)
        ask_total = sum(float(p) * float(q) for p, q in asks)
        st.depth.bid_total = bid_total
        st.depth.ask_total = ask_total

    # ── 快速信号检测 ──────────────────────────────────────────────────────────

    def _detect_fast_signals(self, symbol: str, st: _SymbolState, now: float):
        """在每次成交 tick 时检测快速信号"""
        trades = list(st.trades)

        # 1. 量涌 >3x (5m)
        vol_5m = self._volume_window(trades, now, 300)
        last5 = list(st.volume_1m_samples)[-5:] if len(st.volume_1m_samples) >= 5 else list(st.volume_1m_samples)
        avg_5m = (sum(last5) / len(last5)) * 5 if last5 else 0
        if avg_5m > 0 and vol_5m / avg_5m >= VOLUME_SURGE_FAST_SIGNAL:
            self._emit_signal(symbol, "volume_surge_5m", {
                "surge_ratio": round(vol_5m / avg_5m, 2),
                "vol_5m": round(vol_5m, 0),
                "avg_5m": round(avg_5m, 0),
            })

        # 2. 价格大幅变动 >5% (15m)
        price_change_15m = self._price_change(st.price_ticks, now, 900)
        if abs(price_change_15m) >= PRICE_MOVE_FAST_SIGNAL:
            self._emit_signal(symbol, "price_move_15m", {
                "price_change_pct": round(price_change_15m, 2),
            })

        # 3. 大单集群: 1m 内 >5 笔大单
        trades_1m = [t for t in trades if now - t.ts <= 60]
        large_1m = sum(1 for t in trades_1m if t.usdt >= LARGE_TRADE_USDT)
        if large_1m >= LARGE_TRADE_CLUSTER:
            self._emit_signal(symbol, "large_trade_cluster_1m", {
                "large_trade_count": large_1m,
            })

    def _emit_signal(self, symbol: str, signal_type: str, extra: dict):
        """发射快速信号 (去重: 同类型信号 60s 内只发一次)"""
        now = time.time()
        # 检查是否已有同类信号在 60s 内
        for sig in self._fast_signals:
            if sig["symbol"] == symbol and sig["type"] == signal_type:
                if now - sig["ts"] < 60:
                    return
        sig = {"symbol": symbol, "type": signal_type, "ts": now}
        sig.update(extra)
        self._fast_signals.append(sig)
        logger.info(f"[FastSignal] {symbol} {signal_type} {extra}")

    # ── 辅助函数 ─────────────────────────────────────────────────────────────

    def _find_symbol(self, sym_lower: str) -> Optional[str]:
        """根据小写 symbol 查找已注册的大写 symbol"""
        for sym in self._states:
            if sym.lower() == sym_lower:
                return sym
        return None

    @staticmethod
    def _volume_window(trades: list, now: float, seconds: int) -> float:
        return sum(t.usdt for t in trades if now - t.ts <= seconds)

    @staticmethod
    def _price_change(price_ticks: deque, now: float, seconds: int) -> float:
        """返回过去 seconds 秒的价格变动百分比"""
        ticks = [(ts, p) for ts, p in price_ticks if now - ts <= seconds]
        if len(ticks) < 2:
            return 0.0
        old_price = ticks[0][1]
        new_price = ticks[-1][1]
        if old_price <= 0:
            return 0.0
        return (new_price - old_price) / old_price * 100

    @staticmethod
    def _empty_metrics() -> dict:
        return {
            "symbol": "",
            "volume_surge_1m": 1.0,
            "volume_surge_5m": 1.0,
            "price_change_5m": 0.0,
            "price_change_15m": 0.0,
            "trade_intensity": 0.0,
            "large_trade_count_5m": 0,
            "bid_ask_imbalance": 0.5,
        }
