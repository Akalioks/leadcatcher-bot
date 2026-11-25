"""The questionnaire dialog, exactly as a person goes through it in Telegram."""
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot import texts
from bot.keyboards import NavCb
from tests.conftest import ANNA, BOB


async def test_full_questionnaire_makes_a_hot_lead(make_env):
    env = await make_env()
    await env.say(ANNA, "/start fb_spring")
    assert "SunBay Solar" in env.session.texts(1)[0] and "Anna" in env.session.texts(1)[0]
    assert "1/7" in env.session.last(1).text and len(env.buttons(1)) == 4

    await env.press(ANNA, env.button(1, "Private house"))
    edited = env.session.of(EditMessageText, 1)[-1]
    assert "✅ <b>🏠 Private house</b>" in edited.text  # the answered question shows the choice
    assert "2/7" in env.session.last(1).text and any("Back" in b.text for b in env.buttons(1))
    await env.press(ANNA, env.button(1, "€150–300"))
    await env.press(ANNA, env.button(1, "Within a month"))

    city_q = env.session.last(1)
    assert "4/7" in city_q.text and texts.TAP_OR_TYPE in city_q.text  # people know they can type
    assert [b.text for b in env.buttons(1)][:4] == ["Tallinn", "Tartu", "Pärnu", "Narva"]
    await env.say(ANNA, "Saue")  # a town that isn't on the buttons
    assert (await env.repo.draft_of(1)).answers["city"] == "Saue"
    await env.press(ANNA, env.button(1, "Back"))
    await env.press(ANNA, env.button(1, "Tallinn"))  # or one tap
    assert (await env.repo.draft_of(1)).answers["city"] == "Tallinn"
    assert texts.TYPE_HINT in env.session.last(1).text  # the name question: no buttons, so "type your answer"
    await env.say(ANNA, "A")
    assert "at least 2" in env.session.last(1).text
    await env.say(ANNA, "Anna")

    phone_q = env.session.last(1)
    assert "6/7" in phone_q.text and isinstance(phone_q.reply_markup, ReplyKeyboardMarkup)
    assert phone_q.reply_markup.keyboard[0][0].request_contact
    await env.share_contact(ANNA, "37255551234")
    ack = env.session.of(SendMessage, 1)[-2]
    assert isinstance(ack.reply_markup, ReplyKeyboardRemove)  # the phone keyboard disappears

    await env.say(ANNA, "not an email")
    assert "doesn't look like an email" in env.session.last(1).text
    await env.press(ANNA, env.button(1, "Skip"))
    summary = env.session.last(1).text
    assert "check your request" in summary and "+37255551234" in summary and "Email:</b> —" in summary

    await env.press(ANNA, env.button(1, "Send request"))
    lead = (await env.repo.recent(1))[0]
    assert (lead.status, lead.temperature, lead.score, lead.source) == ("new", "hot", 85, "fb_spring")
    assert (lead.name, lead.phone, lead.email) == ("Anna", "+37255551234", "")
    assert any("Thank you, Anna" in t and "within 15 minutes" in t for t in env.session.texts(1))

    card = env.session.texts(99)[-1]
    assert "🔥 HOT <b>lead #1</b> · score 85/100" in card and "Anna (@anna)" in card
    assert "📞 +37255551234" in card and "Source: fb_spring · Telegram" in card and "call within 15 min" in card
    assert "<b>Location:</b> Tallinn" in card and "<b>Phone:</b>" not in card  # phone isn't repeated
    assert [b.text for b in env.buttons(99)] == ["🙋 I'll take it", "🗑 Spam / not relevant"]
    assert any("Demo: this is what the sales team receives" in t for t in env.session.texts(1))
    assert len((await env.repo.lead(1)).cards) == 2  # owner + demo copy

    await env.press(ANNA, NavCb(lead=1, step=7, action="send").pack())  # double tap on "Send"
    assert env.answers()[-1] == "Your request has already been sent ✅"
    assert sum("lead #1" in t for t in env.session.texts(99)) == 1


async def test_back_stale_buttons_and_wrong_input(make_env):
    env = await make_env()
    await env.say(ANNA, "/start")
    first = env.button(1, "Private house")
    await env.press(ANNA, first)
    await env.press(ANNA, first)  # an old button from an answered question
    assert env.answers()[-1] == "This question is no longer active."
    assert (await env.repo.draft_of(1)).step == 1

    await env.say(ANNA, "a lot")  # typing when buttons are expected
    assert env.session.of(SendMessage, 1)[-2].text == texts.PICK_BUTTON and "2/7" in env.session.last(1).text

    await env.press(ANNA, env.button(1, "Back"))
    assert (await env.repo.draft_of(1)).step == 0 and "1/7" in env.session.last(1).text

    await env.press(BOB, env.button(1, "Apartment"))  # someone else can't answer for Anna
    assert env.answers()[-1] == "This question is no longer active."
    await env.say(BOB, "hello")
    assert env.session.last(2).text == texts.NO_DRAFT


async def test_phone_step_back_and_other_peoples_contacts(make_env):
    env = await make_env()
    await env.say(ANNA, "/start")
    for label in ("Private house", "Over €300", "This year"):
        await env.press(ANNA, env.button(1, label))
    await env.say(ANNA, "Tartu")
    await env.say(ANNA, "Anna")
    await env.share_contact(ANNA, "+37255550000", owner_id=777)  # a friend's contact card
    assert env.session.last(1).text == texts.OWN_PHONE
    await env.say(ANNA, "call me maybe")
    assert "doesn't look like a phone" in env.session.last(1).text
    await env.say(ANNA, texts.BACK)  # the Back button of the phone keyboard
    assert isinstance(env.session.of(SendMessage, 1)[-2].reply_markup, ReplyKeyboardRemove)
    assert "5/7" in env.session.last(1).text
    await env.say(ANNA, "Anna K")
    await env.say(ANNA, "+372 5555 0000")
    await env.say(ANNA, "anna@example.ee")
    assert "check your request" in env.session.last(1).text
    await env.say(ANNA, "is it ok?")  # text on the summary
    assert env.session.of(SendMessage, 1)[-2].text == texts.TAP_SEND


async def test_restart_and_one_draft_per_person(make_env):
    env = await make_env()
    lead = await env.fill_in(source="", timeline="Not sure yet", bill="Under €50", property="Just exploring",
                             email="a@b.ee")
    assert (lead.temperature, lead.score, lead.source) == ("cold", 5, "")
    assert any("guide on solar savings" in t for t in env.session.texts(1))
    assert "call within" not in env.session.texts(99)[-1]  # no deadline for cold leads

    await env.say(ANNA, "/start ig")
    await env.press(ANNA, env.button(1, "Private house"))
    await env.say(ANNA, "/start ig")  # starting again resets the same draft
    draft = await env.repo.draft_of(1)
    assert draft.step == 0 and draft.answers == {}
    leads = await env.repo.all_leads()
    assert len(leads) == 1  # drafts aren't leads


async def test_summary_restart_button(make_env):
    env = await make_env()
    await env.say(ANNA, "/start")
    for label in ("Private house", "Over €300", "This year"):
        await env.press(ANNA, env.button(1, label))
    for text in ("Tartu", "Anna", "+372 5555 0000", "anna@example.ee"):
        await env.say(ANNA, text)
    await env.press(ANNA, env.button(1, "Start over"))
    draft = await env.repo.draft_of(1)
    assert draft.step == 0 and draft.answers == {} and "1/7" in env.session.last(1).text
