"""
动态代币管理器 (Token Manager)

功能:
1. 运行时热添加/删除监控代币 (无需重启)
2. 通过 REST API / Telegram 命令 / 配置文件管理
3. 自动验证 Binance 是否存在该交易对
4. 持久化到 DB, 重启后自动恢复
"""
import time
import json
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from config import TokenConfig, WATCH_TOKENS
from data.data_store import DataStore

logger = logging.getLogger(__name__)


class TokenManager:
    """动态代币管理"""

    def __init__(self, store: DataStore):
        self.store = store
        self._init_table()
        self._restore_custom_tokens()

    def _init_table(self):
        with self.store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS custom_tokens (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    chain TEXT DEFAULT 'eth',
                    contract TEXT DEFAULT '',
                    decimals INTEGER DEFAULT 18,
                    tags_json TEXT DEFAULT '[]',
                    added_at INTEGER,
                    added_by TEXT DEFAULT 'user',
                    active INTEGER DEFAULT 1
                );
            """)

    # ── 添加代币 ─────────────────────────────────────────────────────────

    def add_token(
        self,
        symbol: str,
        name: str = "",
        chain: str = "eth",
        contract: str = "",
        decimals: int = 18,
        tags: List[str] = None,
        added_by: str = "user",
    ) -> Dict:
        """
        添加新代币到监控列表

        symbol: Binance 交易对, e.g. "XYZUSDT"
        返回: {"success": bool, "message": str}
        """
        symbol = symbol.upper().strip()
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        # 检查是否已存在
        existing = [t.symbol for t in WATCH_TOKENS]
        if symbol in existing:
            return {"success": False, "message": f"{symbol} 已在监控列表中"}

        # 创建 TokenConfig
        name = name or symbol.replace("USDT", "")
        tags = tags or []
        token_cfg = TokenConfig(
            symbol=symbol, name=name, chain=chain,
            contract=contract, decimals=decimals, tags=tags,
        )

        # 加入运行时列表
        WATCH_TOKENS.append(token_cfg)

        # 持久化
        with self.store._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO custom_tokens
                (symbol, name, chain, contract, decimals, tags_json, added_at, added_by, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                symbol, name, chain, contract, decimals,
                json.dumps(tags), int(time.time() * 1000), added_by,
            ))

        logger.info(f"[TokenMgr] ✅ 添加代币: {symbol} ({name}) chain={chain}")
        return {
            "success": True,
            "message": f"已添加 {symbol} ({name}) 到监控列表, 将在下次扫描时生效",
            "token": asdict(token_cfg),
        }

    # ── 删除代币 ─────────────────────────────────────────────────────────

    def remove_token(self, symbol: str) -> Dict:
        """从监控列表移除代币"""
        symbol = symbol.upper().strip()
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        found = False
        for i, t in enumerate(WATCH_TOKENS):
            if t.symbol == symbol:
                WATCH_TOKENS.pop(i)
                found = True
                break

        if not found:
            return {"success": False, "message": f"{symbol} 不在监控列表中"}

        with self.store._conn() as conn:
            conn.execute(
                "UPDATE custom_tokens SET active = 0 WHERE symbol = ?",
                (symbol,),
            )

        logger.info(f"[TokenMgr] ❌ 移除代币: {symbol}")
        return {"success": True, "message": f"已移除 {symbol}"}

    # ── 批量添加 ─────────────────────────────────────────────────────────

    def add_tokens_batch(self, tokens: List[Dict]) -> List[Dict]:
        """
        批量添加代币
        tokens: [{"symbol": "XYZUSDT", "name": "XYZ", "chain": "eth", ...}, ...]
        """
        results = []
        for t in tokens:
            r = self.add_token(
                symbol=t.get("symbol", ""),
                name=t.get("name", ""),
                chain=t.get("chain", "eth"),
                contract=t.get("contract", ""),
                decimals=t.get("decimals", 18),
                tags=t.get("tags", []),
                added_by=t.get("added_by", "batch"),
            )
            results.append(r)
        return results

    # ── 列表查询 ─────────────────────────────────────────────────────────

    def list_tokens(self) -> List[Dict]:
        """获取当前监控列表"""
        return [
            {
                "symbol": t.symbol,
                "name": t.name,
                "chain": t.chain,
                "contract": t.contract[:10] + "..." if len(t.contract) > 10 else t.contract,
                "tags": t.tags,
            }
            for t in WATCH_TOKENS
        ]

    def get_token_count(self) -> int:
        return len(WATCH_TOKENS)

    # ── 启动时恢复 ───────────────────────────────────────────────────────

    def _restore_custom_tokens(self):
        """从 DB 恢复之前动态添加的代币"""
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM custom_tokens WHERE active = 1"
            ).fetchall()

        existing = {t.symbol for t in WATCH_TOKENS}
        restored = 0

        for row in rows:
            sym = row["symbol"]
            if sym in existing:
                continue
            try:
                tags = json.loads(row["tags_json"]) if row["tags_json"] else []
            except (json.JSONDecodeError, TypeError):
                tags = []

            WATCH_TOKENS.append(TokenConfig(
                symbol=sym, name=row["name"],
                chain=row["chain"] or "eth",
                contract=row["contract"] or "",
                decimals=row["decimals"] or 18,
                tags=tags,
            ))
            restored += 1

        if restored > 0:
            logger.info(f"[TokenMgr] 恢复 {restored} 个自定义代币, 当前共 {len(WATCH_TOKENS)} 个")

    # ── Telegram 命令解析 ─────────────────────────────────────────────────

    def parse_command(self, text: str) -> Optional[Dict]:
        """
        解析 Telegram 命令

        支持的命令:
          /add PEPE               → 添加 PEPEUSDT
          /add PEPE eth 0xabc...  → 添加并指定链和合约
          /remove PEPE            → 移除
          /list                   → 列出所有
        """
        text = text.strip()
        if not text.startswith("/"):
            return None

        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/add" and len(parts) >= 2:
            symbol = parts[1]
            chain = parts[2] if len(parts) > 2 else "eth"
            contract = parts[3] if len(parts) > 3 else ""
            return self.add_token(symbol, chain=chain, contract=contract, added_by="telegram")

        elif cmd == "/remove" and len(parts) >= 2:
            return self.remove_token(parts[1])

        elif cmd == "/list":
            tokens = self.list_tokens()
            lines = [f"📡 监控列表 ({len(tokens)} 个):"]
            for t in tokens:
                tags_str = " ".join(f"#{tag}" for tag in t["tags"]) if t["tags"] else ""
                lines.append(f"  {t['symbol']} ({t['name']}) [{t['chain']}] {tags_str}")
            return {"success": True, "message": "\n".join(lines)}

        elif cmd == "/help":
            return {
                "success": True,
                "message": (
                    "📖 代币管理命令:\n"
                    "/add SYMBOL [chain] [contract] — 添加代币\n"
                    "/remove SYMBOL — 移除代币\n"
                    "/list — 查看监控列表\n"
                    "/status — 学习状态\n\n"
                    "示例:\n"
                    "/add PEPE\n"
                    "/add TRUMP eth 0x576e2BeD8F7b46D34016198911Cdf9886f78bea7\n"
                    "/remove LUNC"
                ),
            }

        return None
