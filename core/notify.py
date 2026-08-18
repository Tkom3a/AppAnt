from __future__ import annotations

import httpx


class Notify:
    def __init__(
        self,
        ntfy_url: str,
        ntfy_topic: str,
        telegram_bot_token: str,
        telegram_chat_id: str,
        get_expo_tokens,
    ) -> None:
        self.ntfy_url = ntfy_url.rstrip("/")
        self.ntfy_topic = ntfy_topic.strip()
        self.telegram_bot_token = telegram_bot_token.strip()
        self.telegram_chat_id = str(telegram_chat_id).strip()
        self.get_expo_tokens = get_expo_tokens

    async def send(self, title: str, body: str, level: str = "warn") -> None:
        await self._ntfy(title, body, level)
        await self._telegram(title, body)
        await self._expo(title, body)

    async def _ntfy(self, title: str, body: str, level: str) -> None:
        if not self.ntfy_topic:
            return
        priority = {"info": 3, "warn": 4, "crit": 5}.get(level, 4)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    self.ntfy_url,
                    json={
                        "topic": self.ntfy_topic,
                        "title": title,
                        "message": body,
                        "priority": priority,
                        "tags": ["warning" if level != "info" else "white_check_mark"],
                    },
                )
        except httpx.HTTPError:
            return

    async def _telegram(self, title: str, body: str) -> None:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return
        text = f"{title}\n{body}"
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    url,
                    json={
                        "chat_id": self.telegram_chat_id,
                        "text": text,
                    },
                )
        except httpx.HTTPError:
            return

    async def _expo(self, title: str, body: str) -> None:
        tokens = self.get_expo_tokens()
        if not tokens:
            return
        messages = [
            {
                "to": token,
                "title": title,
                "body": body,
                "sound": "default",
                "priority": "high",
            }
            for token in tokens
        ]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    "https://exp.host/--/api/v2/push/send",
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=messages,
                )
        except httpx.HTTPError:
            return
