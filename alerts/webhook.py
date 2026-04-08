"""
通用 Webhook 推送 (Slack / Discord / 飞书 / 钉钉 / 自定义)
"""
import logging
import httpx
import asyncio
from typing import Optional, Dict
from config import WEBHOOK_URL

logger = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(self, url: str = WEBHOOK_URL):
        self.url = url
        self.enabled = bool(url)
        if not self.enabled:
            logger.warning("[Webhook] 未配置 URL, 推送已禁用")

    async def send(self, message: str, extra: Optional[Dict] = None) -> bool:
        if not self.enabled:
            logger.info(f"[Webhook] (禁用) 消息: {message[:80]}...")
            return False

        # 自动检测平台格式
        payload = self._build_payload(message, extra)

        async with httpx.AsyncClient(timeout=15.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(self.url, json=payload)
                    if resp.status_code < 300:
                        logger.info("[Webhook] 发送成功")
                        return True
                    logger.warning(f"[Webhook] HTTP {resp.status_code}: {resp.text[:200]}")
                except Exception as e:
                    logger.error(f"[Webhook] 发送失败 (attempt {attempt+1}): {e}")
                await asyncio.sleep(2 ** attempt)

        return False

    def _build_payload(self, message: str, extra: Optional[Dict] = None) -> Dict:
        """根据 URL 格式自动构建不同平台的 payload"""
        url = self.url.lower()

        # Discord
        if "discord.com/api/webhooks" in url:
            return {"content": message[:2000]}

        # Slack
        if "hooks.slack.com" in url:
            return {"text": message}

        # 飞书
        if "open.feishu.cn" in url or "open.larksuite.com" in url:
            return {
                "msg_type": "text",
                "content": {"text": message},
            }

        # 钉钉
        if "oapi.dingtalk.com" in url:
            return {
                "msgtype": "text",
                "text": {"content": message},
            }

        # 通用格式
        payload = {"message": message, "timestamp": __import__("time").time()}
        if extra:
            payload.update(extra)
        return payload
