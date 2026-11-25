"""The questionnaire in a private chat: questions one by one, back / skip, a summary, then the lead goes to sales.
Progress is stored in the database, so a restart of the bot doesn't lose anyone's half-filled answers."""
import re
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from .. import keyboards as kb
from .. import notify, texts
from ..config import Config
from ..db import Lead
from ..funnel import Funnel, validate_answer
from ..keyboards import AnswerCb, NavCb
from ..repo import Repo
from ..webhooks import Webhooks

router = Router(name="funnel")
router.message.filter(F.chat.type == "private")

_NOT_ALLOWED_IN_SOURCE = re.compile(r"[^A-Za-z0-9_\-]")


def clean_source(raw: str | None) -> str:
    """t.me/bot?start=fb_spring → "fb_spring". Telegram allows only these characters anyway."""
    return _NOT_ALLOWED_IN_SOURCE.sub("", raw or "")[:64]


async def ask(bot: Bot, chat_id: int, lead: Lead, funnel: Funnel) -> None:
    if lead.step >= len(funnel.steps):
        await bot.send_message(chat_id, texts.summary(lead, funnel), reply_markup=kb.summary(lead))
    else:
        step = funnel.steps[lead.step]
        await bot.send_message(chat_id, texts.question(funnel, lead.step), reply_markup=kb.question(lead, step))


async def move(bot: Bot, repo: Repo, funnel: Funnel, chat_id: int, lead: Lead,
               answers: dict, to_step: int, ack: str) -> None:
    """Saves the answers and asks the question at to_step. Hides the phone keyboard when leaving that question."""
    leaving_phone = lead.step < len(funnel.steps) and funnel.steps[lead.step].type == "phone"
    lead = await repo.update_draft(lead.id, answers, to_step)
    if leaving_phone:
        await bot.send_message(chat_id, ack, reply_markup=ReplyKeyboardRemove())
    await ask(bot, chat_id, lead, funnel)


async def _hide_buttons(cq: CallbackQuery, new_text: str | None = None) -> None:
    if isinstance(cq.message, Message):
        with suppress(TelegramBadRequest):
            if new_text:
                await cq.message.edit_text(new_text, reply_markup=None)
            else:
                await cq.message.edit_reply_markup(reply_markup=None)


async def _active_draft(repo: Repo, cq: CallbackQuery, lead_id: int, step: int | None) -> Lead | None:
    """The person's own unfinished questionnaire, and the button belongs to the current question."""
    lead = await repo.lead(lead_id)
    if lead is None or lead.user_id != cq.from_user.id or lead.status != "draft" or \
            (step is not None and lead.step != step):
        await cq.answer("This question is no longer active.")
        return None
    return lead


@router.message(CommandStart())
async def start(m: Message, command: CommandObject, repo: Repo, funnel: Funnel) -> None:
    lead = await repo.start_draft(m.from_user, clean_source(command.args))
    await m.answer(texts.intro(funnel, m.from_user.first_name), reply_markup=ReplyKeyboardRemove())
    await ask(m.bot, m.chat.id, lead, funnel)


@router.callback_query(AnswerCb.filter())
async def on_choice(cq: CallbackQuery, callback_data: AnswerCb, bot: Bot, repo: Repo, funnel: Funnel) -> None:
    lead = await _active_draft(repo, cq, callback_data.lead, callback_data.step)
    if lead is None:
        return
    step = funnel.steps[lead.step]
    if step.type not in ("choice", "text") or not 0 <= callback_data.opt < len(step.options):
        await cq.answer()
        return
    label = step.options[callback_data.opt].label
    await cq.answer()
    await _hide_buttons(cq, texts.answered(funnel, lead.step, label))
    await move(bot, repo, funnel, cq.from_user.id, lead, {**lead.answers, step.id: label}, lead.step + 1, "✅")


@router.callback_query(NavCb.filter())
async def on_nav(cq: CallbackQuery, callback_data: NavCb, bot: Bot, cfg: Config, repo: Repo,
                 funnel: Funnel, hooks: Webhooks) -> None:
    action, chat_id = callback_data.action, cq.from_user.id

    if action == "send":
        await send_lead(cq, callback_data, bot, cfg, repo, funnel, hooks)
        return

    lead = await _active_draft(repo, cq, callback_data.lead, None if action == "resume" else callback_data.step)
    if lead is None:
        return
    await cq.answer()
    if action == "resume":
        await _hide_buttons(cq)
        await ask(bot, chat_id, lead, funnel)
    elif action == "back" and lead.step > 0:
        await _hide_buttons(cq)
        await move(bot, repo, funnel, chat_id, lead, lead.answers, lead.step - 1, "⬅️")
    elif action == "skip" and lead.step < len(funnel.steps) and funnel.steps[lead.step].optional:
        step = funnel.steps[lead.step]
        await _hide_buttons(cq, texts.answered(funnel, lead.step, "skipped"))
        await move(bot, repo, funnel, chat_id, lead, {**lead.answers, step.id: ""}, lead.step + 1, "➡️")
    elif action == "restart":
        await _hide_buttons(cq)
        await move(bot, repo, funnel, chat_id, lead, {}, 0, "🔄")


async def send_lead(cq: CallbackQuery, data: NavCb, bot: Bot, cfg: Config, repo: Repo,
                    funnel: Funnel, hooks: Webhooks) -> None:
    lead = await repo.lead(data.lead)
    if lead is None or lead.user_id != cq.from_user.id:
        await cq.answer()
        return
    lead = await repo.complete(lead.id, funnel)
    if lead is None:  # second tap, or an old summary message
        await cq.answer("Your request has already been sent ✅")
        await _hide_buttons(cq)
        return
    await cq.answer("Sent ✅")
    await _hide_buttons(cq, texts.summary(lead, funnel) + "\n\n✅ <b>Sent</b>")
    await bot.send_message(cq.from_user.id, texts.finish(lead, funnel))
    notify.in_background(notify.new_lead(bot, cfg, repo, funnel, lead))
    await hooks.enqueue("lead.created", lead, funnel)
    notify.in_background(hooks.deliver_due())


@router.message(F.text | F.contact)
async def on_text(m: Message, repo: Repo, funnel: Funnel) -> None:
    if m.text and m.text.startswith("/"):
        await m.answer("Unknown command. " + texts.NO_DRAFT)
        return
    lead = await repo.draft_of(m.from_user.id)
    if lead is None:
        await m.answer(texts.NO_DRAFT)
        return
    bot, chat_id = m.bot, m.chat.id

    if lead.step >= len(funnel.steps):  # on the summary
        if m.text == texts.BACK:
            await move(bot, repo, funnel, chat_id, lead, lead.answers, lead.step - 1, "⬅️")
        else:
            await m.answer(texts.TAP_SEND)
            await ask(bot, chat_id, lead, funnel)
        return

    step = funnel.steps[lead.step]
    if m.text == texts.BACK and lead.step > 0:
        await move(bot, repo, funnel, chat_id, lead, lead.answers, lead.step - 1, "⬅️")
        return
    if m.text == texts.SKIP and step.optional:
        await move(bot, repo, funnel, chat_id, lead, {**lead.answers, step.id: ""}, lead.step + 1, "➡️")
        return
    if step.type == "choice":
        await m.answer(texts.PICK_BUTTON)
        await ask(bot, chat_id, lead, funnel)
        return

    if m.contact:
        if step.type != "phone":
            await m.answer(texts.step_hint(step))
            return
        if m.contact.user_id and m.contact.user_id != m.from_user.id:
            await m.answer(texts.OWN_PHONE)
            return
        raw = m.contact.phone_number if m.contact.phone_number.startswith("+") else "+" + m.contact.phone_number
    else:
        raw = m.text or ""

    value, error = validate_answer(step, raw)
    if error:  # repeat the question's buttons (Back / Skip / Share phone) under the error
        await m.answer(error, reply_markup=kb.question(lead, step))
        return
    await move(bot, repo, funnel, chat_id, lead, {**lead.answers, step.id: value}, lead.step + 1, "✅ Got it!")


@router.message()
async def anything_else(m: Message, repo: Repo, funnel: Funnel) -> None:
    """Photos, stickers, voice messages..."""
    lead = await repo.draft_of(m.from_user.id)
    if lead and lead.step < len(funnel.steps):
        await m.answer(texts.step_hint(funnel.steps[lead.step]))
    else:
        await m.answer(texts.NO_DRAFT)
