"""Settings are read from the .env file (see .env.example)."""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


def _list(raw: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    leads_chat_id: int | None      # sales team group. Empty = leads go to every admin in private
    demo_mode: bool
    tz: ZoneInfo
    database_url: str
    funnel_file: str
    nudge_after_min: int           # remind people who stopped half-way. 0 = off
    webhook_urls: tuple[str, ...]  # Google Sheets (Apps Script), Zapier, Make, n8n, your CRM...
    webhook_secret: str            # signs webhook bodies (X-Signature header)
    allowed_origins: tuple[str, ...]  # websites allowed to send forms. "*" = any
    trust_proxy: bool              # take the visitor's IP from X-Forwarded-For (behind Caddy/nginx)
    host: str
    port: int

    def now(self) -> datetime:
        return datetime.now(self.tz).replace(tzinfo=None)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    @property
    def sales_chats(self) -> list[int]:
        return [self.leads_chat_id] if self.leads_chat_id else sorted(self.admin_ids)


def load_config(**overrides) -> Config:
    load_dotenv(".env")
    env = os.getenv
    chat = env("LEADS_CHAT_ID", "").strip()
    values: dict[str, Any] = dict(
        bot_token=env("BOT_TOKEN", ""),
        admin_ids=_ids(env("ADMIN_IDS", "")),
        leads_chat_id=int(chat) if chat else None,
        demo_mode=_bool(env("DEMO_MODE", "true")),
        tz=ZoneInfo(env("TIMEZONE", "Europe/Tallinn")),
        database_url=env("DATABASE_URL", "sqlite+aiosqlite:///leads.db"),
        funnel_file=env("FUNNEL_FILE", "funnel.json"),
        nudge_after_min=int(env("NUDGE_AFTER_MIN", "60")),
        webhook_urls=_list(env("WEBHOOK_URLS", "")),
        webhook_secret=env("WEBHOOK_SECRET", ""),
        allowed_origins=_list(env("ALLOWED_ORIGINS", "*")),
        trust_proxy=_bool(env("TRUST_PROXY", "false")),
        host=env("HOST", "0.0.0.0"),
        port=int(env("PORT", "8080")),
    )
    values.update(overrides)
    return Config(**values)
