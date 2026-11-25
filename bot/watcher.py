"""Background checks once a minute. State lives in the database, so restarts are safe.
- a hot lead nobody took in time → alert in the sales chat
- someone stopped half-way through the questions → one friendly reminder"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import ReplyParameters

from . import keyboards as kb
from . import notify, texts
from .config import Config
from .funnel import Funnel
from .repo import Repo

log = logging.getLogger(__name__)


async def check_once(bot: Bot, cfg: Config, repo: Repo, funnel: Funnel) -> tuple[int, int]:
    alerts = nudges = 0
    for lead in await repo.due_sla(funnel.sla_minutes):
        text = texts.sla_alert(lead, funnel.sla_minutes[lead.temperature])
        for c in lead.cards:
            await notify.send(bot, c.chat_id, text, reply_parameters=ReplyParameters(
                message_id=c.message_id, allow_sending_without_reply=True))
        await repo.mark(lead.id, sla_alerted_at=cfg.now())
        alerts += 1
    if cfg.nudge_after_min > 0:
        for lead in await repo.due_nudges(cfg.nudge_after_min):
            if lead.user_id and lead.step < len(funnel.steps):
                await notify.send(bot, lead.user_id, texts.nudge(lead, funnel), reply_markup=kb.resume(lead))
                nudges += 1
            await repo.mark(lead.id, nudged_at=cfg.now())
    return alerts, nudges


async def watch(bot: Bot, cfg: Config, repo: Repo, funnel: Funnel, every_sec: float = 60) -> None:
    while True:
        try:
            alerts, nudges = await check_once(bot, cfg, repo, funnel)
            if alerts or nudges:
                log.info("Sent %d SLA alert(s), %d reminder(s)", alerts, nudges)
        except Exception:
            log.exception("Watcher error")
        await asyncio.sleep(every_sec)
