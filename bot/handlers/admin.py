"""Commands for the sales team: /stats, /leads, /export, /chatid.
In demo mode anyone can try them: /stats shows totals only, /leads and /export show only the visitor's own leads."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from .. import texts
from ..config import Config
from ..export import to_csv
from ..funnel import Funnel
from ..repo import Repo

router = Router(name="admin")
ALL = object()


def _scope(m: Message, cfg: Config):
    """ALL for the team, the visitor's id in demo mode, None if not allowed."""
    if cfg.is_admin(m.from_user.id) or (cfg.leads_chat_id and m.chat.id == cfg.leads_chat_id):
        return ALL
    return m.from_user.id if cfg.demo_mode else None


@router.message(Command("stats"))
async def stats(m: Message, cfg: Config, repo: Repo) -> None:
    if _scope(m, cfg) is None:
        await m.answer(texts.TEAM_ONLY)
        return
    await m.answer(texts.stats(await repo.stats()))


@router.message(Command("leads"))
async def leads(m: Message, cfg: Config, repo: Repo) -> None:
    scope = _scope(m, cfg)
    if scope is None:
        await m.answer(texts.TEAM_ONLY)
        return
    items = await repo.recent(None if scope is ALL else scope, limit=15)
    if not items:
        await m.answer("No leads yet. Send /start to try the questionnaire.")
        return
    head = "📋 <b>Latest leads</b>" + ("" if scope is ALL else " <i>(demo: only yours)</i>")
    await m.answer(head + "\n\n" + "\n".join(texts.lead_line(l) for l in items))


@router.message(Command("export"))
async def export(m: Message, cfg: Config, repo: Repo, funnel: Funnel) -> None:
    scope = _scope(m, cfg)
    if scope is None:
        await m.answer(texts.TEAM_ONLY)
        return
    items = await repo.all_leads(None if scope is ALL else scope)
    if not items:
        await m.answer("Nothing to export yet.")
        return
    name = f"leads_{cfg.now():%Y-%m-%d}.csv"
    await m.answer_document(BufferedInputFile(to_csv(items, funnel), filename=name),
                            caption=f"📎 {len(items)} lead(s). Opens in Excel and Google Sheets.")


@router.message(Command("chatid"))
async def chat_id(m: Message) -> None:
    """Setup helper: add the bot to the sales group, send /chatid there, put the number into LEADS_CHAT_ID."""
    if m.chat.type == "private":
        await m.answer(f"Your Telegram id: <code>{m.from_user.id}</code>\n"
                       "Put it into ADMIN_IDS. For a sales group, add me there and send /chatid in the group.")
    else:
        await m.answer(f"This chat's id: <code>{m.chat.id}</code>\nPut it into LEADS_CHAT_ID in .env and restart the bot.")
