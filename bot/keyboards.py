"""Buttons."""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import texts
from .db import Lead
from .funnel import Step


class AnswerCb(CallbackData, prefix="a"):
    lead: int
    step: int
    opt: int


class NavCb(CallbackData, prefix="n"):
    lead: int
    step: int
    action: str  # back | skip | send | restart | resume


class SalesCb(CallbackData, prefix="s"):
    lead: int
    action: str  # taken | contacted | won | lost


def question(lead: Lead, step: Step) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    i = lead.step
    if step.type == "phone":  # Telegram can share the number in one tap, but only with a reply keyboard
        rows = [[KeyboardButton(text=texts.SHARE_PHONE, request_contact=True)]]
        nav = ([KeyboardButton(text=texts.BACK)] if i > 0 else []) + \
              ([KeyboardButton(text=texts.SKIP)] if step.optional else [])
        if nav:
            rows.append(nav)
        return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)

    b = InlineKeyboardBuilder()
    for n, opt in enumerate(step.options):
        b.button(text=opt.label, callback_data=AnswerCb(lead=lead.id, step=i, opt=n))
    nav = []
    if i > 0:
        nav.append(InlineKeyboardButton(text=texts.BACK, callback_data=NavCb(lead=lead.id, step=i, action="back").pack()))
    if step.optional:
        nav.append(InlineKeyboardButton(text=texts.SKIP, callback_data=NavCb(lead=lead.id, step=i, action="skip").pack()))
    if step.options:  # short answers two per row, long ones one per row
        b.adjust(2 if all(len(o.label) <= 16 for o in step.options) else 1)
    if nav:
        b.row(*nav)
    markup = b.as_markup()
    return markup if markup.inline_keyboard else None


def summary(lead: Lead) -> InlineKeyboardMarkup:
    cb = lambda a: NavCb(lead=lead.id, step=lead.step, action=a).pack()  # noqa: E731
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Send request", callback_data=cb("send"))],
        [InlineKeyboardButton(text=texts.BACK, callback_data=cb("back")),
         InlineKeyboardButton(text="🔄 Start over", callback_data=cb("restart"))],
    ])


def resume(lead: Lead) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="▶️ Continue", callback_data=NavCb(lead=lead.id, step=lead.step, action="resume").pack())]])


def sales(lead: Lead) -> InlineKeyboardMarkup | None:
    cb = lambda a: SalesCb(lead=lead.id, action=a).pack()  # noqa: E731
    rows = {
        "new": [[InlineKeyboardButton(text="🙋 I'll take it", callback_data=cb("taken"))],
                [InlineKeyboardButton(text="🗑 Spam / not relevant", callback_data=cb("lost"))]],
        "taken": [[InlineKeyboardButton(text="📞 Contacted", callback_data=cb("contacted"))],
                  [InlineKeyboardButton(text="✅ Won", callback_data=cb("won")),
                   InlineKeyboardButton(text="❌ Lost", callback_data=cb("lost"))]],
        "contacted": [[InlineKeyboardButton(text="✅ Won", callback_data=cb("won")),
                       InlineKeyboardButton(text="❌ Lost", callback_data=cb("lost"))]],
    }.get(lead.status)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
