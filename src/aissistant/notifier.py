import asyncio
import time

import httpx

from .config import required


class BotNotifier:
    def __init__(self, client=None):
        self.token = required("TELEGRAM_BOT_TOKEN")
        self.owner = int(required("TELEGRAM_OWNER_CHAT_ID"))
        if self.owner <= 0:
            raise ValueError("Private owner ID required")
        self.client = client or httpx.AsyncClient(timeout=20)
        self.verified = False
        self.next_send = 0.0
        self.cooldown = 0.0

    async def _call(self, method, payload):
        if method not in {"getChat", "sendMessage"} or payload.get("chat_id") != self.owner:
            raise ValueError("Only owner notifications are permitted")
        response = await self.client.post(
            f"https://api.telegram.org/bot{self.token}/{method}",
            json=payload,
        )
        if response.status_code == 429:
            self.cooldown = time.monotonic() + max(
                1, int(response.json().get("parameters", {}).get("retry_after", 60))
            )
            raise ValueError("Bot API rate limited")
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError("Bot API rejected request")
        return data["result"]

    async def send(self, text: str):
        if time.monotonic() < self.cooldown:
            raise ValueError("Bot API cooldown active")
        await asyncio.sleep(max(0, self.next_send - time.monotonic()))
        if not self.verified:
            chat = await self._call("getChat", {"chat_id": self.owner})
            if chat.get("type") != "private" or int(chat["id"]) != self.owner:
                raise ValueError("Owner must be verified private chat")
            self.verified = True
        if len(text.encode("utf-16-le")) // 2 > 3900:
            raise ValueError("Notification too long")
        await self._call(
            "sendMessage",
            {
                "chat_id": self.owner,
                "text": text,
                "link_preview_options": {"is_disabled": True},
            },
        )

        self.next_send = time.monotonic() + 1.1

    async def close(self):
        await self.client.aclose()


class SyntheticNotifier:
    async def send(self, text: str):
        # Validate representation, but never log even synthetic message bodies.
        if not text or len(text.encode("utf-16-le")) // 2 > 3900:
            raise ValueError("Invalid synthetic summary")

    async def close(self):
        pass
