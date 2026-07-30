from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    default_chat_id: str
    allowed_chat_ids: set[str]

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.default_chat_id)


def parse_chat_ids(value: str) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def load_telegram_config() -> TelegramConfig:
    default_chat_id = (os.getenv("TELEGRAM_DEFAULT_CHAT_ID") or "").strip()
    allowed_chat_ids = parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS") or "")
    if default_chat_id:
        allowed_chat_ids.add(default_chat_id)
    return TelegramConfig(
        bot_token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(),
        default_chat_id=default_chat_id,
        allowed_chat_ids=allowed_chat_ids,
    )


def telegram_config_status(config: Optional[TelegramConfig] = None) -> dict:
    config = config or load_telegram_config()
    return {
        "enabled": config.enabled,
        "has_bot_token": bool(config.bot_token),
        "has_default_chat_id": bool(config.default_chat_id),
        "allowed_chat_count": len(config.allowed_chat_ids),
    }


def send_telegram_message(
    chat_id: str,
    text: str,
    *,
    config: Optional[TelegramConfig] = None,
    opener: Callable = urlopen,
    reply_markup: Optional[dict] = None,
) -> dict:
    config = config or load_telegram_config()
    target_chat_id = str(chat_id or "").strip()
    message = str(text or "").strip()

    if not config.bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    if not target_chat_id:
        raise ValueError("Telegram chat ID is required")
    if config.allowed_chat_ids and target_chat_id not in config.allowed_chat_ids:
        raise PermissionError("Telegram chat ID is not allowed")
    if not message:
        raise ValueError("Telegram message is required")

    token = quote(config.bot_token, safe=":")
    body = {
        "chat_id": target_chat_id,
        "text": message[:4096],
        "disable_web_page_preview": True,
    }
    if reply_markup:
        body["reply_markup"] = reply_markup

    request = Request(
        f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API rejected the message: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Telegram API: {exc.reason}") from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API returned an error: {payload}")
    return payload
