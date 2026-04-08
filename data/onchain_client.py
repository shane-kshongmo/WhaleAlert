"""
链上数据采集客户端
- Etherscan / BscScan API: 代币转账记录, 持仓分布
- 交易所标签地址库: 识别资金流向
"""
import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import asyncio

from config import ETHERSCAN_API_KEY, BSCSCAN_API_KEY

logger = logging.getLogger(__name__)

# 已知的主要交易所地址 (以太坊, 部分)
# 实际部署时建议从 Etherscan labels / Arkham 获取完整列表
KNOWN_EXCHANGE_ADDRESSES = {
    # Binance
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0x236f9f97e0e62388479bf9e5ba4889e46b0273c3": "OKX",
    # Bybit
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
    # Coinbase
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": "Coinbase",
    # Kucoin
    "0xd6216fc19db775df9774a6e33526131da7d19a2c": "Kucoin",
    # Gate.io
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    # Huobi/HTX
    "0xab5c66752a9e8167967685f1450532fb96d5d24f": "HTX",
}


@dataclass
class TransferRecord:
    """代币转账记录"""
    tx_hash: str
    block: int
    timestamp: int
    from_addr: str
    to_addr: str
    value: float            # 代币数量
    value_usd: float        # 估算 USD 价值
    from_label: str = ""    # 已知标签 (交易所名等)
    to_label: str = ""


@dataclass
class OnchainMetrics:
    """链上指标汇总"""
    symbol: str
    timestamp: int

    # 资金流向
    exchange_inflow_count: int = 0     # 转入交易所笔数
    exchange_outflow_count: int = 0    # 从交易所转出笔数
    exchange_inflow_volume: float = 0  # 转入交易所金额
    exchange_outflow_volume: float = 0 # 从交易所转出金额
    net_flow: float = 0               # 净流向 (正=净流出, 利多)

    # 大额转账
    large_transfer_count: int = 0      # 大额转账笔数 (>$50K)
    whale_transfer_count: int = 0      # 巨鲸转账笔数 (>$500K)

    # 持仓分布 (需要额外API, 此处标记)
    top10_holders_pct: float = 0       # Top10 持仓占比
    top50_holders_pct: float = 0       # Top50 持仓占比
    holder_count: int = 0              # 总持仓地址数
    holder_count_change_7d: int = 0    # 7日地址数变化

    # 活跃度
    unique_senders: int = 0
    unique_receivers: int = 0
    transfer_count: int = 0


class OnchainClient:
    """链上数据采集"""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self._rate_limiter = asyncio.Semaphore(3)  # Etherscan 免费版限制

    async def close(self):
        await self.client.aclose()

    def _get_api_url(self, chain: str) -> Tuple[str, str]:
        """根据链返回 API URL 和 Key"""
        mapping = {
            "eth": ("https://api.etherscan.io/api", ETHERSCAN_API_KEY),
            "bsc": ("https://api.bscscan.com/api", BSCSCAN_API_KEY),
            "arb": ("https://api.arbiscan.io/api", ETHERSCAN_API_KEY),  # 通常同一个 key
        }
        return mapping.get(chain, (None, None))

    def _label_address(self, addr: str) -> str:
        """标注已知地址"""
        return KNOWN_EXCHANGE_ADDRESSES.get(addr.lower(), "")

    def _is_exchange(self, addr: str) -> bool:
        return addr.lower() in KNOWN_EXCHANGE_ADDRESSES

    # ── 代币转账记录 ─────────────────────────────────────────────────────

    async def get_token_transfers(
        self, contract: str, chain: str = "eth",
        days: int = 30, page: int = 1, offset: int = 1000,
    ) -> List[TransferRecord]:
        """获取代币转账记录"""
        api_url, api_key = self._get_api_url(chain)
        if not api_url or not api_key or not contract:
            return []

        start_block = 0  # 简化: 获取最近的即可

        async with self._rate_limiter:
            try:
                resp = await self.client.get(api_url, params={
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": contract,
                    "page": page,
                    "offset": offset,
                    "sort": "desc",
                    "apikey": api_key,
                })
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"[Onchain] 转账获取失败 {contract}: {e}")
                return []

        if data.get("status") != "1" or not data.get("result"):
            return []

        cutoff = int((datetime.utcnow() - timedelta(days=days)).timestamp())
        records = []

        for tx in data["result"]:
            ts = int(tx.get("timeStamp", 0))
            if ts < cutoff:
                continue

            decimals = int(tx.get("tokenDecimal", 18))
            value = int(tx.get("value", 0)) / (10 ** decimals)
            from_addr = tx.get("from", "").lower()
            to_addr = tx.get("to", "").lower()

            records.append(TransferRecord(
                tx_hash=tx.get("hash", ""),
                block=int(tx.get("blockNumber", 0)),
                timestamp=ts,
                from_addr=from_addr,
                to_addr=to_addr,
                value=value,
                value_usd=0,  # 需要配合价格计算
                from_label=self._label_address(from_addr),
                to_label=self._label_address(to_addr),
            ))

        await asyncio.sleep(0.25)  # rate limit
        return records

    # ── 持仓分布 (Top Holders) ───────────────────────────────────────────

    async def get_top_holders(
        self, contract: str, chain: str = "eth",
    ) -> Dict:
        """
        获取 Top Holders 分布
        注意: Etherscan 免费 API 不直接提供 top holders
        实际部署建议用:
          - Etherscan Pro API (付费)
          - Dune Analytics API
          - Moralis / Alchemy NFT API
          - 自建 indexer
        此处实现一个基于转账记录的简化估算
        """
        # 简化: 通过分析转账记录估算持仓分布
        transfers = await self.get_token_transfers(contract, chain, days=90, offset=5000)
        if not transfers:
            return {"top10_pct": 0, "top50_pct": 0, "holder_count": 0}

        # 构建余额快照 (简化估算)
        balances: Dict[str, float] = {}
        for tx in sorted(transfers, key=lambda x: x.timestamp):
            balances[tx.from_addr] = balances.get(tx.from_addr, 0) - tx.value
            balances[tx.to_addr] = balances.get(tx.to_addr, 0) + tx.value

        # 过滤掉负余额和零余额 (数据不完整导致)
        positive = {k: v for k, v in balances.items() if v > 0}
        if not positive:
            return {"top10_pct": 0, "top50_pct": 0, "holder_count": 0}

        total = sum(positive.values())
        sorted_holders = sorted(positive.values(), reverse=True)
        top10 = sum(sorted_holders[:10]) / total * 100 if total > 0 else 0
        top50 = sum(sorted_holders[:50]) / total * 100 if total > 0 else 0

        return {
            "top10_pct": round(top10, 1),
            "top50_pct": round(top50, 1),
            "holder_count": len(positive),
        }

    # ── 综合指标计算 ─────────────────────────────────────────────────────

    async def analyze_token(
        self, symbol: str, contract: str, chain: str = "eth",
        current_price: float = 0,
        large_threshold_usd: float = 50000,
        whale_threshold_usd: float = 500000,
    ) -> OnchainMetrics:
        """计算某代币的完整链上指标"""
        metrics = OnchainMetrics(symbol=symbol, timestamp=int(time.time() * 1000))

        if not contract or chain not in ("eth", "bsc", "arb"):
            logger.info(f"[Onchain] {symbol}: 不支持链上分析 (chain={chain})")
            return metrics

        # 获取转账记录
        transfers = await self.get_token_transfers(contract, chain, days=30)
        if not transfers:
            return metrics

        metrics.transfer_count = len(transfers)
        senders = set()
        receivers = set()

        for tx in transfers:
            value_usd = tx.value * current_price if current_price > 0 else 0
            tx.value_usd = value_usd
            senders.add(tx.from_addr)
            receivers.add(tx.to_addr)

            # 资金流向分析
            from_is_exchange = self._is_exchange(tx.from_addr)
            to_is_exchange = self._is_exchange(tx.to_addr)

            if to_is_exchange and not from_is_exchange:
                # 散户/庄家 → 交易所 (可能要卖)
                metrics.exchange_inflow_count += 1
                metrics.exchange_inflow_volume += value_usd
            elif from_is_exchange and not to_is_exchange:
                # 交易所 → 冷钱包 (可能在吸筹)
                metrics.exchange_outflow_count += 1
                metrics.exchange_outflow_volume += value_usd

            # 大额转账
            if value_usd >= large_threshold_usd:
                metrics.large_transfer_count += 1
            if value_usd >= whale_threshold_usd:
                metrics.whale_transfer_count += 1

        metrics.unique_senders = len(senders)
        metrics.unique_receivers = len(receivers)
        metrics.net_flow = metrics.exchange_outflow_volume - metrics.exchange_inflow_volume

        # 持仓分布
        holder_data = await self.get_top_holders(contract, chain)
        metrics.top10_holders_pct = holder_data.get("top10_pct", 0)
        metrics.top50_holders_pct = holder_data.get("top50_pct", 0)
        metrics.holder_count = holder_data.get("holder_count", 0)

        logger.info(
            f"[Onchain] {symbol}: "
            f"transfers={metrics.transfer_count}, "
            f"inflow={metrics.exchange_inflow_count}, "
            f"outflow={metrics.exchange_outflow_count}, "
            f"large={metrics.large_transfer_count}, "
            f"top10={metrics.top10_holders_pct:.1f}%"
        )
        return metrics
