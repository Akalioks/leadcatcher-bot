"""Messages to the sales team: the lead card with buttons, and keeping every copy of it up to date."""
import asyncio
import logging
from collections.abc import Coroutine

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from . import keyboards as kb
from . import texts
from .config import Config
from .db import Lead
from .funnel import Funnel
from .repo import Repo

log = logging.getLogger(__name__)
_pending: set[asyncio.Task] = set()


def in_background(coro: Coroutine) -> None:
    """Runs without making the person wait for Telegram or a CRM."""
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain() -> None:
    """Waits for background work to finish (on shutdown, and in tests)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def send(bot: Bot, chat_id: int, text: str, **kwargs) -> Message | None:
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramAPIError as e:
        log.warning("Could not message %s: %s", chat_id, e)
        return None


async def new_lead(bot: Bot, cfg: Config, repo: Repo, funnel: Funnel, lead: Lead) -> None:
    text, markup = texts.card(lead, funnel), kb.sales(lead)
    if not cfg.sales_chats:
        log.warning("Lead #%s: set LEADS_CHAT_ID or ADMIN_IDS in .env, nobody received it", lead.id)
    for chat_id in cfg.sales_chats:
        m = await send(bot, chat_id, text, reply_markup=markup)
        if m:
            await repo.add_card(lead.id, chat_id, m.message_id)
    if cfg.demo_mode and lead.user_id and not cfg.is_admin(lead.user_id) and lead.user_id not in cfg.sales_chats:
        m = await send(bot, lead.user_id, texts.demo_card(text), reply_markup=markup)
        if m:
            await repo.add_card(lead.id, lead.user_id, m.message_id, demo_copy=True)


async def refresh_cards(bot: Bot, repo: Repo, funnel: Funnel, lead_id: int) -> None:
    """After a status change, every copy of the card shows the same status and the right buttons."""
    lead = await repo.lead(lead_id)
    if lead is None:
        return
    text, markup = texts.card(lead, funnel), kb.sales(lead)
    for c in lead.cards:
        try:
            await bot.edit_message_text(text=texts.demo_card(text) if c.demo_copy else text,
                                        chat_id=c.chat_id, message_id=c.message_id, reply_markup=markup)
        except TelegramAPIError as e:
            if "not modified" not in str(e):
                log.warning("Could not update lead card in %s: %s", c.chat_id, e)
