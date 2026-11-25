"""Buttons on the lead card: take the lead, mark it contacted, won or lost."""
from aiogram import Bot, Router
from aiogram.types import CallbackQuery, Message

from .. import notify, texts
from ..config import Config
from ..funnel import Funnel
from ..keyboards import SalesCb
from ..repo import Repo
from ..webhooks import Webhooks

router = Router(name="sales")

DONE = {"taken": "The lead is yours 🙋", "contacted": "Marked as contacted 📞", "won": "Won! 🎉", "lost": "Marked as lost"}


@router.callback_query(SalesCb.filter())
async def on_sales(cq: CallbackQuery, callback_data: SalesCb, bot: Bot, cfg: Config, repo: Repo,
                   funnel: Funnel, hooks: Webhooks) -> None:
    lead = await repo.lead(callback_data.lead)
    if lead is None or callback_data.action not in DONE:
        await cq.answer("Lead not found.")
        return
    uid = cq.from_user.id
    in_sales_chat = bool(cfg.leads_chat_id) and isinstance(cq.message, Message) and cq.message.chat.id == cfg.leads_chat_id
    demo_own = cfg.demo_mode and lead.user_id == uid  # a demo visitor can play with their own lead only
    if not (cfg.is_admin(uid) or in_sales_chat or demo_own):
        await cq.answer("Only the sales team can do this.", show_alert=True)
        return

    lead, error = await repo.set_status(lead.id, callback_data.action, uid, cq.from_user.full_name)
    if error:
        await cq.answer(error, show_alert=True)
        await notify.refresh_cards(bot, repo, funnel, lead.id)  # someone else was faster: show the real status
        return
    await cq.answer(DONE[callback_data.action])
    await notify.refresh_cards(bot, repo, funnel, lead.id)
    if callback_data.action == "taken" and lead.user_id:
        await notify.send(bot, lead.user_id, texts.taken_for_client(lead, funnel))
    await hooks.enqueue("lead.updated", lead, funnel)
    notify.in_background(hooks.deliver_due())
