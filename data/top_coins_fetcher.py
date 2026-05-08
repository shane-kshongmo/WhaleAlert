"""
Top-10 市值代币获取器
从 CoinGecko 动态获取当前市值前10的代币, 每24h刷新
"""
import time
import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)

# Stable coins and wrapped tokens to exclude from top-10 results
_STABLECOIN_IDS = {
    "tether", "usd-coin", "binance-usd", "dai", "trueusd",
    "first-digital-usd", "usdd", "pax-dollar", "frax", "usde",
    "ethena-usde", "paypal-usd", "rlusd", "usds", "sky-usds",
    "usual-usd", "tether-gold", "paxos-gold",
}
# Symbols that look like fiat/stablecoins (suffix match) - belt-and-suspenders
_STABLE_SYMBOL_SUFFIXES = ("usdt", "usdc", "busd", "tusd", "usds", "usdp", "gusd", "husd")
_WRAPPED_PREFIXES = ("wrapped-", "staked-", "bridged-", "lido-")
# Exchange-native tokens and other non-tradable assets to exclude
_EXCLUDED_IDS = {
    "whitebit",      # WhiteBIT exchange token
    "leo-token",     # Bitfinex LEO
    "okb",           # OKX OKB
    "kucoin-shares", # KuCoin KCS
    "gate",          # Gate.io GT
    "figure-heloc",  # Mortgage-backed security token
}

_COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=30&page=1&sparkline=false"
)


class TopCoinsFetcher:
    """Fetches and caches the top-10 coins by market cap from CoinGecko."""

    FALLBACK_LIST: List[str] = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "TONUSDT",
    ]

    # BTC and ETH are fundamentals-driven; all others use whale/accumulation detection
    TREND_COINS = {"BTCUSDT", "ETHUSDT"}

    def __init__(self) -> None:
        self._cache: List[str] = []
        self._last_fetch: float = 0
        self._cache_ttl: float = 86400.0  # 24 hours

    async def refresh(self) -> List[str]:
        """Fetch top-10 from CoinGecko, updating the cache. Falls back gracefully."""
        if self._cache and (time.time() - self._last_fetch) < self._cache_ttl:
            return self._cache

        result = await self._fetch_from_coingecko()
        if result:
            self._cache = result
            self._last_fetch = time.time()
            logger.info(f"[TopCoins] Refreshed: {result}")
        else:
            if not self._cache:
                self._cache = list(self.FALLBACK_LIST)
                logger.warning("[TopCoins] CoinGecko fetch failed, using fallback list")
            else:
                logger.warning("[TopCoins] CoinGecko fetch failed, retaining previous cache")

        return self._cache

    async def _fetch_from_coingecko(self) -> List[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_COINGECKO_URL)
                resp.raise_for_status()
                data = resp.json()

            result: List[str] = []
            for coin in data:
                coin_id = coin.get("id", "")
                symbol = coin.get("symbol", "")
                if not symbol or not symbol.isalnum():
                    continue
                if coin_id in _STABLECOIN_IDS:
                    continue
                if any(coin_id.startswith(p) for p in _WRAPPED_PREFIXES):
                    continue
                if coin_id in _EXCLUDED_IDS:
                    continue
                sym_lower = symbol.lower()
                if any(sym_lower.endswith(s) for s in _STABLE_SYMBOL_SUFFIXES):
                    continue
                binance_sym = symbol.upper() + "USDT"
                result.append(binance_sym)
                if len(result) == 10:
                    break

            return result if len(result) == 10 else []
        except Exception as e:
            logger.warning(f"[TopCoins] CoinGecko fetch error: {e}")
            return []

    def get_top_coins(self) -> List[str]:
        """Synchronous accessor — returns cached list or fallback."""
        return self._cache if self._cache else list(self.FALLBACK_LIST)

    def get_coin_strategy(self, symbol: str) -> str:
        """Returns 'trend' for BTC/ETH, 'whale' for all other top-10 coins."""
        return "trend" if symbol in self.TREND_COINS else "whale"

    def is_top_coin(self, symbol: str) -> bool:
        return symbol in self.get_top_coins()

    def to_token_configs(self):
        """Returns TokenConfig objects for each top-10 coin (for watchlist injection)."""
        from config import TokenConfig
        configs = []
        for sym in self.get_top_coins():
            name = sym.replace("USDT", "")
            configs.append(TokenConfig(
                symbol=sym,
                name=name,
                chain="unknown",
                contract="",
                decimals=18,
                tags=["top10"],
            ))
        return configs
