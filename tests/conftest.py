"""Test helpers: a fake Telegram API that records every call, and a ready bot with its own database."""
import itertools
from datetime import datetime
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User
from aiohttp.test_utils import TestClient, TestServer

from bot import notify
from bot.app import build_dispatcher
from bot.config import load_config
from bot.funnel import load_funnel
from bot.handlers import admin, funnel_flow, sales
from bot.repo import Repo
from bot.web import RateLimiter, create_web_app
from bot.webhooks import Webhooks

TOKEN = "42:TEST-token"
FUNNEL = Path(__file__).resolve().parent.parent / "funnel.json"

ANNA = User(id=1, is_bot=False, first_name="Anna", username="anna")
BOB = User(id=2, is_bot=False, first_name="Bob")
OWNER = User(id=99, is_bot=False, first_name="Max", last_name="Sales")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.ids = itertools.count(1000)
        self.blocked: set[int] = set()

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        chat_id = getattr(method, "chat_id", None)
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
        if isinstance(method, (SendMessage, EditMessageText, SendDocument)):
            mid = getattr(method, "message_id", None) or next(self.ids)
            return Message(message_id=mid, date=datetime.now(), chat=Chat(id=chat_id or 0, type="private"),
                           text=getattr(method, "text", None))
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def close(self):
        pass

    def of(self, cls, chat_id=None):
        return [m for m in self.calls if isinstance(m, cls) and (chat_id is None or m.chat_id == chat_id)]

    def texts(self, chat_id) -> list[str]:
        return [m.text for m in self.calls if isinstance(m, (SendMessage, EditMessageText)) and m.chat_id == chat_id]

    def last(self, chat_id) -> SendMessage:
        return self.of(SendMessage, chat_id)[-1]


class Env:
    def __init__(self, cfg, repo, funnel):
        self.cfg, self.repo, self.funnel = cfg, repo, funnel
        self.session = FakeSession()
        self.bot = Bot(TOKEN, session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.hooks = Webhooks(cfg, repo, timeout_sec=3)
        for r in (admin.router, sales.router, funnel_flow.router):
            r._parent_router = None  # module-level routers attach once; each test gets a fresh dispatcher
        self.dp = build_dispatcher(cfg, repo, funnel, self.hooks)
        self.update_ids = itertools.count(1)
        self.limiter = RateLimiter(limit=5)

    async def feed(self, **update):
        await self.dp.feed_update(self.bot, Update(update_id=next(self.update_ids), **update))
        await notify.drain()

    async def say(self, user: User, text: str | None = None, chat_id: int | None = None, **kw):
        chat = Chat(id=chat_id or user.id, type="private" if chat_id is None else "supergroup")
        await self.feed(message=Message(message_id=next(self.update_ids), date=datetime.now(), chat=chat,
                                        from_user=user, text=text, **kw))

    async def share_contact(self, user: User, phone: str, owner_id: int | None = None):
        await self.say(user, contact=Contact(phone_number=phone, first_name=user.first_name,
                                             user_id=user.id if owner_id is None else owner_id))

    async def press(self, user: User, data: str, chat_id: int | None = None, message_id: int = 1):
        chat = Chat(id=chat_id or user.id, type="private" if chat_id is None else "supergroup")
        msg = Message(message_id=message_id, date=datetime.now(), chat=chat, text="…")
        await self.feed(callback_query=CallbackQuery(id="cq", from_user=user, chat_instance="ci", data=data, message=msg))

    def buttons(self, chat_id) -> list:
        """Inline buttons of the last message sent to this chat."""
        markup = self.last_markup(chat_id)
        return [b for row in markup.inline_keyboard for b in row]

    def last_markup(self, chat_id):
        return self.session.last(chat_id).reply_markup

    def button(self, chat_id, text: str) -> str:
        for b in self.buttons(chat_id):
            if text in b.text:
                return b.callback_data
        raise AssertionError(f"no button {text!r} in {[b.text for b in self.buttons(chat_id)]}")

    def answers(self) -> list[str]:
        from aiogram.methods import AnswerCallbackQuery
        return [m.text for m in self.session.of(AnswerCallbackQuery)]

    async def client(self) -> TestClient:
        c = TestClient(TestServer(create_web_app(self.cfg, self.repo, self.funnel, self.hooks, self.bot, self.limiter)))
        await c.start_server()
        return c

    async def fill_in(self, user: User = ANNA, property="Private house", bill="€150–300", timeline="Within a month",
                      city="Tallinn", name="Anna", phone="+372 5555 1234", email=None, source="fb_spring"):
        """Goes through the whole questionnaire and sends the request."""
        await self.say(user, "/start" + (f" {source}" if source else ""))
        for label in (property, bill, timeline):
            await self.press(user, self.button(user.id, label))
        for text in (city, name, phone):
            await self.say(user, text)
        if email:
            await self.say(user, email)
        else:
            await self.press(user, self.button(user.id, "Skip"))
        await self.press(user, self.button(user.id, "Send request"))
        return (await self.repo.recent(user.id, limit=1))[0]


@pytest.fixture
async def make_env(tmp_path):
    made = []

    async def factory(**overrides):
        settings = {"bot_token": TOKEN, "database_url": f"sqlite+aiosqlite:///{tmp_path}/l{len(made)}.db",
                    "admin_ids": frozenset({99}), "leads_chat_id": None, "demo_mode": True, "webhook_urls": (),
                    "webhook_secret": "", "allowed_origins": ("*",), "trust_proxy": False, "nudge_after_min": 60,
                    **overrides}
        cfg = load_config(**settings)
        env = Env(cfg, await Repo.create(cfg), load_funnel(FUNNEL))
        made.append(env)
        return env

    yield factory
    for e in made:
        await e.hooks.close()
        await e.repo.close()
