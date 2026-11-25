"""Sending leads to Google Sheets / Zapier / a CRM: a real local HTTP server plays the CRM."""
import hashlib
import hmac
import json
from datetime import timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer
from sqlalchemy import update

from bot import notify
from bot.db import Outbox
from bot.keyboards import SalesCb
from tests.conftest import OWNER


class FakeCrm:
    def __init__(self):
        self.received: list[tuple[dict, str]] = []
        self.answer: tuple[int, str] = (200, '{"ok": true}')
        app = web.Application()
        app.router.add_post("/hook", self.hook)
        self.server = TestServer(app)

    async def hook(self, request: web.Request) -> web.Response:
        body = await request.read()
        status, text = self.answer
        if status == 200 and '"ok": true' in text:
            self.received.append((json.loads(body), request.headers.get("X-Signature", "")))
        return web.Response(status=status, text=text, content_type="application/json")

    @property
    def url(self) -> str:
        return str(self.server.make_url("/hook"))


async def make(make_env, **kw):
    crm = FakeCrm()
    await crm.server.start_server()
    env = await make_env(webhook_urls=(crm.url,), webhook_secret="s3cret", **kw)
    return env, crm


async def make_due_now(env):
    async with env.repo.session() as s:
        await s.execute(update(Outbox).values(next_at=env.cfg.now() - timedelta(seconds=1)))
        await s.commit()


async def test_lead_and_status_change_are_sent_signed(make_env):
    env, crm = await make(make_env)
    lead = await env.fill_in()
    await notify.drain()
    (data, signature), = crm.received
    assert data["event"] == "lead.created"
    sent = data["lead"]
    assert (sent["id"], sent["temperature"], sent["score"], sent["phone"], sent["telegram"]) == \
           (lead.id, "hot", 85, "+372 5555 1234", "@anna")
    assert sent["answers"]["Location"] == "Tallinn" and sent["answers"]["Property"] == "🏠 Private house"
    body = json.dumps(data, ensure_ascii=False).encode()
    assert signature == "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()

    await env.press(OWNER, SalesCb(lead=lead.id, action="taken").pack())
    await notify.drain()
    assert crm.received[-1][0]["event"] == "lead.updated" and crm.received[-1][0]["lead"]["manager"] == "Max Sales"
    assert all(i.delivered_at for i in await env.repo.outbox_items())
    await crm.server.close()


async def test_crm_down_then_back(make_env):
    env, crm = await make(make_env)
    crm.answer = (500, "Internal error")
    await env.fill_in()
    await notify.drain()
    item, = await env.repo.outbox_items()
    assert item.attempts == 1 and not item.delivered_at and "HTTP 500" in item.last_error
    assert item.next_at > env.cfg.now()  # will retry in a minute
    assert await env.hooks.deliver_due() == 0  # not yet

    crm.answer = (200, '{"ok": true}')
    await make_due_now(env)
    assert await env.hooks.deliver_due() == 1
    assert len(crm.received) == 1 and (await env.repo.outbox_items())[0].delivered_at
    await crm.server.close()


async def test_apps_script_style_errors_and_giving_up(make_env):
    env, crm = await make(make_env)
    crm.answer = (200, '{"ok": false, "error": "forbidden"}')  # Google Apps Script reports errors like this
    await env.fill_in()
    await notify.drain()
    for _ in range(10):
        await make_due_now(env)
        await env.hooks.deliver_due()
    item, = await env.repo.outbox_items()
    assert item.failed and item.attempts == 6 and "rejected: forbidden" in item.last_error

    url = crm.url
    await crm.server.close()  # nobody listening at all
    env2 = await make_env(webhook_urls=(url,))
    await env2.fill_in()
    await notify.drain()
    item, = await env2.repo.outbox_items()
    assert item.attempts == 1 and "Error" in item.last_error


async def test_crashed_apps_script_is_not_a_delivery(make_env):
    crm = FakeCrm()
    await crm.server.start_server()
    url = crm.url + "?host=script.google.com"  # treated as an Apps Script address
    env2 = await make_env(webhook_urls=(url,))
    crm.answer = (200, "<html><body>TypeError: Cannot read properties of null</body></html>")
    await env2.fill_in()
    await notify.drain()
    item, = await env2.repo.outbox_items()
    assert not item.delivered_at and "Apps Script did not confirm" in item.last_error and "TypeError" in item.last_error

    crm.answer = (200, '{"ok": true}')
    await make_due_now(env2)
    assert await env2.hooks.deliver_due() == 1
    await crm.server.close()


async def test_no_webhooks_configured(make_env):
    env = await make_env()
    await env.fill_in()
    await notify.drain()
    assert await env.repo.outbox_items() == []
