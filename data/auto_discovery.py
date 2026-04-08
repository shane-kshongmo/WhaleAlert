"""
自动发现扫描器 (Auto Discovery Scanner)

自动从币安获取所有 USDT 交易对, 通过多级筛选找到值得深度监控的代币

架构: 三级漏斗
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Level 1: 全量获取 (Binance exchangeInfo)
           ~600+ USDT pairs
                    ↓ 过滤: 交易状态/最低成交额/排除稳定币
  Level 2: 轻量预扫 (24h ticker, 无需K线)
           ~200-300 pairs
                    ↓ 过滤: 成交额排名/价格异常/波动率筛选
  Level 3: 候选池 (进入正式监控)
           ~50-100 pairs → WATCH_TOKENS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

扫描频率: 每 4 小时执行一次 (代币列表变化慢, 无需高频)
"""
import time
import logging
import asyncio
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

import httpx

from config import TokenConfig, WATCH_TOKENS, BINANCE_API_KEY
from data.data_store import DataStore

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"


# ═══════════════════════════════════════════════════════════════════════════
# 筛选配置
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DiscoveryConfig:
    """自动发现的筛选参数"""

    # Level 1: 基础过滤
    min_quote_volume_24h: float = 500_000     # 最低 24h 成交额 (USDT)
    max_quote_volume_24h: float = 5_000_000_000  # 排除 BTC/ETH 等巨鲸 (50亿)
    excluded_bases: Set[str] = field(default_factory=lambda: {
        "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDD", "USDP",  # 稳定币
        "BTCB", "WBTC", "WETH", "STETH", "CBETH",                 # wrapped
        "BTTC",                                                     # 极低价垃圾
    })
    excluded_symbols: Set[str] = field(default_factory=lambda: {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",   # 大盘, 不可能被控盘
        "ADAUSDT", "DOTUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
    })

    # Level 2: 行情预筛 (这些信号提示"值得关注")
    # 吸筹特征: 缩量横盘
    interesting_vol_drop_pct: float = -40    # 24h成交额 vs 7日均值降超40%
    # 异动特征: 突然放量
    interesting_vol_surge_x: float = 2.5     # 成交额突增 2.5 倍
    # 价格异动
    interesting_price_change_min: float = -5  # 24h 涨跌幅在 -5% ~ +8% (横盘区间)
    interesting_price_change_max: float = 8
    # 大幅波动 (可能正在拉盘, 进入监控以追踪后续)
    pump_alert_change_pct: float = 30        # 24h 涨超 30% 直接关注 (可能正向50%冲)

    # Level 3: 候选池大小
    max_watchlist_size: int = 120            # 最多同时监控数量
    min_watchlist_size: int = 30             # 最少保留数量
    # 按成交额排名取 top N 作为保底
    top_volume_always_watch: int = 50        # 成交额 Top50 始终监控

    # 扫描频率
    scan_interval_hours: float = 4.0         # 每 4 小时扫描一次

DISCOVERY_CONFIG = DiscoveryConfig()


# ═══════════════════════════════════════════════════════════════════════════
# 扫描器
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TickerSnapshot:
    """24h ticker 快照"""
    symbol: str
    price: float
    change_pct: float        # 24h 涨跌幅 %
    quote_volume: float      # 24h 成交额 USDT
    trades: int              # 24h 成交笔数
    high: float
    low: float
    # 计算字段
    price_range_pct: float = 0    # (high-low)/low
    vol_rank: int = 0


class AutoDiscoveryScanner:
    """自动发现扫描器"""

    def __init__(self, store: DataStore):
        self.store = store
        self.cfg = DISCOVERY_CONFIG
        self._init_table()
        self._last_scan_time = 0
        self._all_usdt_pairs: List[str] = []
        self._ticker_cache: Dict[str, TickerSnapshot] = {}

    def _init_table(self):
        with self.store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS discovered_tokens (
                    symbol TEXT PRIMARY KEY,
                    first_seen INTEGER,
                    last_seen INTEGER,
                    reason TEXT,
                    quote_volume_24h REAL,
                    change_pct REAL,
                    auto_added INTEGER DEFAULT 1
                );
            """)

    # ── 主入口 ───────────────────────────────────────────────────────────

    async def run_discovery(self) -> Dict:
        """
        执行一轮完整发现扫描

        返回: {"added": [...], "removed": [...], "total": int, "scanned": int}
        """
        now = time.time()
        if now - self._last_scan_time < self.cfg.scan_interval_hours * 3600:
            remaining = self.cfg.scan_interval_hours - (now - self._last_scan_time) / 3600
            return {"skipped": True, "next_scan_hours": round(remaining, 1)}

        logger.info(f"[Discovery] 开始自动发现扫描...")
        start = time.time()

        # Level 1: 获取所有 USDT 交易对
        all_pairs = await self._fetch_all_usdt_pairs()
        logger.info(f"[Discovery] Level 1: 获取 {len(all_pairs)} 个 USDT 交易对")

        # Level 2: 获取 24h ticker, 轻量筛选
        tickers = await self._fetch_all_tickers()
        candidates = self._level2_filter(tickers)
        logger.info(f"[Discovery] Level 2: 筛选出 {len(candidates)} 个候选")

        # Level 3: 整合到 WATCH_TOKENS
        result = self._level3_promote(candidates, tickers)

        self._last_scan_time = now
        elapsed = time.time() - start

        logger.info(
            f"[Discovery] 完成 ({elapsed:.1f}s): "
            f"扫描{len(all_pairs)} → 候选{len(candidates)} → "
            f"新增{len(result['added'])} 移除{len(result['removed'])} "
            f"当前监控{result['total']}"
        )
        return result

    # ── Level 1: 获取所有 USDT 交易对 ────────────────────────────────────

    async def _fetch_all_usdt_pairs(self) -> List[str]:
        """从 Binance exchangeInfo 获取所有活跃的 USDT 交易对"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(f"{BASE_URL}/api/v3/exchangeInfo")
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"[Discovery] exchangeInfo 获取失败: {e}")
                return self._all_usdt_pairs  # 返回缓存

        pairs = []
        for sym_info in data.get("symbols", []):
            symbol = sym_info["symbol"]
            status = sym_info.get("status", "")
            quote = sym_info.get("quoteAsset", "")
            base = sym_info.get("baseAsset", "")

            # 只要 USDT 计价、交易中的
            if (quote == "USDT"
                and status == "TRADING"
                and base not in self.cfg.excluded_bases
                and symbol not in self.cfg.excluded_symbols):
                pairs.append(symbol)

        self._all_usdt_pairs = pairs
        return pairs

    # ── Level 2: 24h Ticker 预筛 ─────────────────────────────────────────

    async def _fetch_all_tickers(self) -> Dict[str, TickerSnapshot]:
        """一次请求获取所有交易对的 24h ticker"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(f"{BASE_URL}/api/v3/ticker/24hr")
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"[Discovery] ticker 获取失败: {e}")
                return self._ticker_cache

        tickers = {}
        for t in data:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            try:
                price = float(t.get("lastPrice", 0))
                qv = float(t.get("quoteVolume", 0))
                change = float(t.get("priceChangePercent", 0))
                trades = int(t.get("count", 0))
                high = float(t.get("highPrice", 0))
                low = float(t.get("lowPrice", 0))

                pr = (high - low) / low * 100 if low > 0 else 0

                tickers[symbol] = TickerSnapshot(
                    symbol=symbol, price=price, change_pct=change,
                    quote_volume=qv, trades=trades, high=high, low=low,
                    price_range_pct=pr,
                )
            except (ValueError, TypeError, ZeroDivisionError):
                continue

        # 按成交额排名
        ranked = sorted(tickers.values(), key=lambda x: -x.quote_volume)
        for i, t in enumerate(ranked):
            t.vol_rank = i + 1
            tickers[t.symbol] = t

        self._ticker_cache = tickers
        return tickers

    def _level2_filter(self, tickers: Dict[str, TickerSnapshot]) -> List[TickerSnapshot]:
        """
        轻量预筛: 用 24h ticker 数据快速判断哪些值得深度分析

        筛选逻辑:
        1. 基础门槛: 成交额 ≥ 50万 USDT
        2. 自动入选: 成交额 Top N
        3. 异动入选: 突然放量 / 缩量横盘 / 价格异动
        """
        cfg = self.cfg
        candidates = []
        seen = set()

        valid = {s: t for s, t in tickers.items()
                 if t.quote_volume >= cfg.min_quote_volume_24h
                 and t.quote_volume <= cfg.max_quote_volume_24h
                 and s in set(self._all_usdt_pairs)}

        # 规则 1: 成交额 Top N 始终监控
        by_vol = sorted(valid.values(), key=lambda x: -x.quote_volume)
        for t in by_vol[:cfg.top_volume_always_watch]:
            if t.symbol not in seen:
                candidates.append(t)
                seen.add(t.symbol)

        # 规则 2: 价格异动 (可能正在被拉盘)
        for t in valid.values():
            if t.symbol in seen:
                continue
            if t.change_pct >= cfg.pump_alert_change_pct:
                candidates.append(t)
                seen.add(t.symbol)

        # 规则 3: 横盘缩量 (吸筹特征)
        for t in valid.values():
            if t.symbol in seen:
                continue
            # 日内波幅很小 + 价格变化小 → 横盘
            if (t.price_range_pct < 5
                and abs(t.change_pct) < 3
                and t.quote_volume >= cfg.min_quote_volume_24h * 2):
                candidates.append(t)
                seen.add(t.symbol)

        # 规则 4: 价格在"安静区间"但成交笔数偏多 (隐蔽吸筹)
        for t in valid.values():
            if t.symbol in seen:
                continue
            if (cfg.interesting_price_change_min < t.change_pct < cfg.interesting_price_change_max
                and t.trades > 50000
                and t.price_range_pct < 8):
                candidates.append(t)
                seen.add(t.symbol)

        return candidates

    # ── Level 3: 整合到监控列表 ──────────────────────────────────────────

    def _level3_promote(
        self, candidates: List[TickerSnapshot], tickers: Dict[str, TickerSnapshot]
    ) -> Dict:
        """将候选代币整合到 WATCH_TOKENS"""

        # 当前已有的 symbol
        current_symbols = {t.symbol for t in WATCH_TOKENS}
        # 手动添加的永不自动移除
        manual_symbols = self._get_manual_symbols()
        candidate_symbols = {c.symbol for c in candidates}

        added = []
        removed = []
        now_ms = int(time.time() * 1000)

        # ── 添加新发现的 ──
        for c in candidates:
            if c.symbol in current_symbols:
                continue
            if len(WATCH_TOKENS) >= self.cfg.max_watchlist_size:
                break

            token_cfg = TokenConfig(
                symbol=c.symbol,
                name=c.symbol.replace("USDT", ""),
                chain="unknown",
                contract="",
                decimals=18,
                tags=self._infer_tags(c),
            )
            WATCH_TOKENS.append(token_cfg)
            added.append(c.symbol)

            with self.store._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO discovered_tokens
                    (symbol, first_seen, last_seen, reason, quote_volume_24h, change_pct, auto_added)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (c.symbol, now_ms, now_ms,
                      self._infer_reason(c), c.quote_volume, c.change_pct))

        # ── 移除不再活跃的 (只移除自动添加的, 不碰手动的) ──
        to_remove = []
        for t in WATCH_TOKENS:
            if t.symbol in manual_symbols:
                continue
            if t.symbol not in candidate_symbols:
                # 检查是否已经不活跃
                ticker = tickers.get(t.symbol)
                if not ticker or ticker.quote_volume < self.cfg.min_quote_volume_24h * 0.5:
                    to_remove.append(t)

        # 保留最低数量
        if len(WATCH_TOKENS) - len(to_remove) >= self.cfg.min_watchlist_size:
            for t in to_remove:
                WATCH_TOKENS.remove(t)
                removed.append(t.symbol)

                with self.store._conn() as conn:
                    conn.execute(
                        "UPDATE discovered_tokens SET auto_added = 0 WHERE symbol = ?",
                        (t.symbol,))

        return {
            "added": added,
            "removed": removed,
            "total": len(WATCH_TOKENS),
            "scanned": len(tickers),
            "candidates": len(candidates),
        }

    # ── 辅助方法 ─────────────────────────────────────────────────────────

    def _get_manual_symbols(self) -> Set[str]:
        """获取手动添加的代币 (不被自动移除)"""
        with self.store._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT symbol FROM custom_tokens WHERE active = 1"
                ).fetchall()
                manual = {r["symbol"] for r in rows}
            except Exception:
                manual = set()
        # config.py 里写死的也是手动的
        hardcoded = {
            "PEPEUSDT", "WIFUSDT", "FLOKIUSDT", "BONKUSDT", "SHIBUSDT",
            "DOGEUSDT", "TURBOUSDT", "NEIROUSDT", "NOTUSDT", "SUIUSDT",
            "TIAUSDT", "FETUSDT", "INJUSDT", "ARBUSDT", "OPUSDT",
            "APEUSDT", "GALAUSDT", "IMXUSDT", "BLURUSDT", "ORDIUSDT",
            "1000SATSUSDT", "PEOPLEUSDT", "LUNCUSDT",
        }
        return manual | hardcoded

    def _infer_tags(self, t: TickerSnapshot) -> List[str]:
        """根据 ticker 特征推断标签"""
        tags = ["auto"]
        sym = t.symbol.replace("USDT", "").lower()

        if t.vol_rank <= 20:
            tags.append("high_vol")
        elif t.vol_rank <= 50:
            tags.append("mid_vol")

        if t.change_pct >= 15:
            tags.append("pumping")
        elif t.price_range_pct < 3:
            tags.append("sideways")

        # 关键词推断
        kw_map = {
            "meme": ["doge", "shib", "pepe", "floki", "bonk", "wif", "meme",
                      "cat", "nyan", "inu", "moon", "elon", "trump", "bome"],
            "ai": ["ai", "fetch", "ocean", "agi", "gpt", "neural"],
            "defi": ["uni", "sushi", "cake", "aave", "comp", "mkr", "crv"],
            "gaming": ["gala", "sand", "mana", "axs", "ilv", "imx", "enj"],
            "l2": ["arb", "op", "matic", "zk", "strk", "manta"],
        }
        for tag, keywords in kw_map.items():
            if any(kw in sym for kw in keywords):
                tags.append(tag)
                break

        return tags

    def _infer_reason(self, t: TickerSnapshot) -> str:
        if t.vol_rank <= 50:
            return f"成交额Top{t.vol_rank}"
        if t.change_pct >= 15:
            return f"24h涨{t.change_pct:+.1f}%"
        if t.price_range_pct < 3:
            return f"横盘缩量(波幅{t.price_range_pct:.1f}%)"
        return f"候选(Vol${t.quote_volume/1e6:.1f}M)"

    # ── 快速价格变化检测 (用于爆涨监控) ──────────────────────────────────

    async def get_all_price_changes(self) -> Dict[str, Dict]:
        """
        获取所有监控代币的价格变化 (用于精确的爆涨检测)

        比 main.py 中的近似值更准确
        """
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
                "volume_avg": ticker.quote_volume,  # 简化
                "trades_24h": ticker.trades,
                "price_range_pct": ticker.price_range_pct,
            }
        return result

    # ── 统计 ─────────────────────────────────────────────────────────────

    def get_discovery_stats(self) -> Dict:
        with self.store._conn() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM discovered_tokens").fetchone()["c"]
            active = conn.execute("SELECT COUNT(*) as c FROM discovered_tokens WHERE auto_added=1").fetchone()["c"]
        return {
            "total_discovered": total,
            "active_auto": active,
            "manual_count": len(self._get_manual_symbols()),
            "watchlist_size": len(WATCH_TOKENS),
            "last_scan": self._last_scan_time,
            "cached_pairs": len(self._all_usdt_pairs),
        }
