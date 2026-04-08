"""
Telegram 推送模块
"""
import logging
import httpx
import asyncio
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


class TelegramBot:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            logger.warning("[Telegram] 未配置 Token/ChatID, 推送已禁用")

    async def send_message(self, text: str, chat_id: Optional[str] = None) -> bool:
        if not self.enabled:
            logger.info(f"[Telegram] (禁用) 消息: {text[:80]}...")
            return False

        target = chat_id or self.chat_id
        # Telegram 消息最大 4096 字符
        if len(text) > 4000:
            text = text[:3997] + "..."

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(f"{self.api_url}/sendMessage", json={
                    "chat_id": target,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                resp.raise_for_status()
                data = resp.json()
                if data.get("ok"):
                    logger.info(f"[Telegram] 发送成功 → {target}")
                    return True
                else:
                    logger.error(f"[Telegram] 发送失败: {data}")
                    return False
            except Exception as e:
                logger.error(f"[Telegram] 发送异常: {e}")
                # 重试一次 (纯文本模式)
                try:
                    resp = await client.post(f"{self.api_url}/sendMessage", json={
                        "chat_id": target,
                        "text": text,
                    })
                    return resp.json().get("ok", False)
                except Exception:
                    return False

    async def send_alert(self, message: str) -> bool:
        """发送预警消息 (带重试)"""
        for attempt in range(3):
            success = await self.send_message(message)
            if success:
                return True
            await asyncio.sleep(2 ** attempt)
        logger.error(f"[Telegram] 3次重试均失败")
        return False

    async def send_photo(self, photo_url: str, caption: str = "") -> bool:
        """发送图片 (用于图表截图等)"""
        if not self.enabled:
            return False
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(f"{self.api_url}/sendPhoto", json={
                    "chat_id": self.chat_id,
                    "photo": photo_url,
                    "caption": caption[:1024] if caption else "",
                })
                return resp.json().get("ok", False)
            except Exception as e:
                logger.error(f"[Telegram] 图片发送失败: {e}")
                return False
