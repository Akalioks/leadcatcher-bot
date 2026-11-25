"""The sales team side: card buttons, the sales group, deadline alerts, reminders, commands."""
import csv
import io
from datetime import timedelta

from aiogram.methods import EditMessageText, SendDocument, SendMessage

from bot import texts
from bot.keyboards import SalesCb
from bot.watcher import check_once
from tests.conftest import ANNA, BOB, OWNER


async def test_owner_takes_and_closes_a_lead(make_env):
    env = await make_env()
    lead = await env.fill_in()
    take = SalesCb(lead=lead.id, action="taken").pack()

    await env.press(BOB, take)  # a stranger
    assert env.answers()[-1] == "Only the sales team can do this."
    await env.press(OWNER, SalesCb(lead=lead.id, action="won").pack())  # can't win before taking
    assert "already new" in env.answers()[-1]

    await env.press(OWNER, take, chat_id=None)
    lead = await env.repo.lead(lead.id)
    assert (lead.status, lead.manager_name) == ("taken", "Max Sales") and lead.taken_at
    edits = env.session.of(EditMessageText)
    assert {e.chat_id for e in edits[-2:]} == {99, 1}  # both copies of the card are updated
    assert "🙋 Taken by Max Sales" in edits[-1].text
    assert [b.text for row in edits[-1].reply_markup.inline_keyboard for b in row] == ["📞 Contacted", "✅ Won", "❌ Lost"]
    assert any("Max Sales</b> from SunBay Solar has your request" in t for t in env.session.texts(1))

    await env.press(OWNER, take)  # a second manager is too late
    assert "already taken" in env.answers()[-1]
    await env.press(OWNER, SalesCb(lead=lead.id, action="contacted").pack())
    await env.press(OWNER, SalesCb(lead=lead.id, action="won").pack())
    lead = await env.repo.lead(lead.id)
    assert lead.status == "won" and lead.closed_at
    assert env.session.of(EditMessageText)[-1].reply_markup is None  # nothing left to press


async def test_demo_visitor_can_try_own_card_only(make_env):
    env = await make_env()
    anna_lead = await env.fill_in(ANNA)
    bob_lead = await env.fill_in(BOB, name="Bob")
    await env.press(ANNA, SalesCb(lead=anna_lead.id, action="taken").pack())
    assert (await env.repo.lead(anna_lead.id)).status == "taken"
    await env.press(ANNA, SalesCb(lead=bob_lead.id, action="taken").pack())
    assert (await env.repo.lead(bob_lead.id)).status == "new"

    real = await make_env(demo_mode=False)
    lead = await real.fill_in(ANNA)
    assert not any("Demo:" in t for t in real.session.texts(1))
    await real.press(ANNA, SalesCb(lead=lead.id, action="taken").pack())
    assert (await real.repo.lead(lead.id)).status == "new"


async def test_sales_group(make_env):
    env = await make_env(leads_chat_id=-500, demo_mode=False)
    lead = await env.fill_in()
    assert any("lead #1" in t for t in env.session.texts(-500)) and env.session.texts(99) == []
    await env.press(BOB, SalesCb(lead=lead.id, action="taken").pack(), chat_id=-500)  # any member of the group
    assert (await env.repo.lead(lead.id)).manager_name == "Bob"
    await env.say(BOB, "/stats", chat_id=-500)
    assert "Leads received: <b>1</b>" in env.session.last(-500).text


async def test_deadline_alert_for_hot_leads(make_env):
    env = await make_env()
    hot = await env.fill_in(ANNA)
    warm = await env.fill_in(BOB, name="Bob", timeline="This year")
    assert warm.temperature == "warm"
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (0, 0)  # not late yet

    await env.repo.mark(hot.id, completed_at=env.cfg.now() - timedelta(minutes=16))
    await env.repo.mark(warm.id, completed_at=env.cfg.now() - timedelta(minutes=30))  # warm has 120 min
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (1, 0)
    alert = [m for m in env.session.of(SendMessage, 99) if "waiting" in m.text][-1]
    assert "lead #1 has been waiting 15 min" in alert.text and alert.reply_parameters.message_id
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (0, 0)  # only once

    await env.press(OWNER, SalesCb(lead=warm.id, action="taken").pack())
    await env.repo.mark(warm.id, completed_at=env.cfg.now() - timedelta(hours=5))
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (0, 0)  # taken leads are fine


async def test_reminder_for_unfinished_questionnaire(make_env):
    env = await make_env()
    await env.say(ANNA, "/start")
    await env.press(ANNA, env.button(1, "Private house"))
    await env.press(ANNA, env.button(1, "Over €300"))
    await env.say(BOB, "/start")  # answered nothing → no reminder
    for uid in (1, 2):
        draft = await env.repo.draft_of(uid)
        await env.repo.mark(draft.id, updated_at=env.cfg.now() - timedelta(minutes=90))
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (0, 1)
    nudge = env.session.last(1)
    assert "<b>5 questions</b> away" in nudge.text
    await env.press(ANNA, env.button(1, "Continue"))
    assert "3/7" in env.session.last(1).text
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (0, 0)  # only once

    await env.say(BOB, "/start")
    await env.press(BOB, env.button(2, "Private house"))
    await env.repo.mark((await env.repo.draft_of(2)).id, updated_at=env.cfg.now() - timedelta(minutes=90))
    env.session.blocked.add(2)  # someone who blocked the bot doesn't stop the checks
    assert await check_once(env.bot, env.cfg, env.repo, env.funnel) == (0, 1)
    assert (await env.repo.draft_of(2)).nudged_at  # and isn't retried forever


async def test_team_commands(make_env):
    env = await make_env()
    await env.fill_in(ANNA)
    await env.fill_in(BOB, name="Bob", source="", email="bob@example.com")
    await env.press(OWNER, SalesCb(lead=1, action="taken").pack())

    await env.say(OWNER, "/stats")
    st = env.session.last(99).text
    assert "Leads received: <b>2</b>" in st and "completion rate 100%" in st and "🔥 Hot 2" in st
    assert "fb_spring 1" in st and "direct 1" in st and "Average time until a manager takes a lead" in st

    await env.say(OWNER, "/leads")
    assert "#2" in env.session.last(99).text and "#1" in env.session.last(99).text
    await env.say(ANNA, "/leads")  # demo visitor sees only own leads
    assert "#1" in env.session.last(1).text and "#2" not in env.session.last(1).text

    await env.say(OWNER, "/export")
    doc = env.session.of(SendDocument, 99)[-1]
    rows = list(csv.reader(io.StringIO(doc.document.data.decode("utf-8-sig"))))
    assert rows[0][:4] == ["ID", "Date", "Channel", "Source"] and "Location" in rows[0]
    assert len(rows) == 3 and rows[2][9] == "bob@example.com" and rows[1][11] == "Max Sales"
    await env.say(ANNA, "/export")
    assert len(env.session.of(SendDocument, 1)[-1].document.data.decode("utf-8-sig").strip().splitlines()) == 2

    await env.say(ANNA, "/chatid")
    assert "Your Telegram id: <code>1</code>" in env.session.last(1).text
    await env.say(ANNA, "/chatid", chat_id=-777)
    assert "<code>-777</code>" in env.session.last(-777).text

    real = await make_env(demo_mode=False)
    await real.say(BOB, "/stats")
    assert real.session.last(2).text == texts.TEAM_ONLY
