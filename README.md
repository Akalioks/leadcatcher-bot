# LeadCatcher — Lead Qualification Bot for Telegram

[Русская версия](README.ru.md) · **Live demo: [@SunBaySolarbot](https://t.me/SunBaySolarbot)**

Turns ad traffic into qualified leads for the sales team. A prospect answers a few button questions in under a minute; the bot scores the lead (hot, warm or cold) and instantly posts it to the sales chat with "take", "contacted" and "won" buttons.

Website form submissions land in the same pipeline: the same chat, CSV and Google Sheets.

The demo is built for a fictional solar panel installer, **SunBay Solar**. The questions live in `funnel.json`.

## Features

**Prospect**
- Arrives from an ad link like `t.me/your_bot?start=fb_spring`. The bot remembers the source (`fb_spring`), so stats show which campaign brings leads.
- Answers with buttons; typed answers are validated (phone, email, length).
- Shares a phone number with one "📱 Share my phone number" button — someone else's contact is rejected.
- Can go back, skip optional questions, review answers before sending, or start over.
- Abandoned half-way? One reminder with a "Continue" button arrives an hour later.
- Progress is stored in the database, so a bot restart doesn't lose answers.

**Sales team**
- Each lead arrives as a card: score, contacts, answers, source and the call-back deadline.
- **🙋 I'll take it** assigns the lead to a manager; nobody else can take it. The prospect gets "Max from SunBay Solar is handling your request".
- Then **📞 Contacted**, **✅ Won**, **❌ Lost**. All copies of the card update at once.
- If a hot lead isn't taken within 15 minutes, the bot nudges the chat by replying to the card. Deadlines are set in `funnel.json`.
- `/stats`: 7-day funnel — started vs. finished, conversion, hot and warm leads, top sources, average time to first response.
- `/leads` lists recent leads; `/export` downloads a CSV for Excel or Google Sheets.
- Shared sales chat: add the bot to a group, send `/chatid` there and put the number into `LEADS_CHAT_ID`.

**Integrations**
- **Google Sheets**: every lead becomes a row, and status changes update the same row. No Google Cloud needed — a 5-minute Apps Script from `integrations/google_sheets.gs`.
- **Webhooks** to Zapier, Make, n8n or any CRM (HubSpot, Pipedrive, amoCRM, …) via their webhook triggers. The body is signed with HMAC (`X-Signature`).
- **Reliable delivery.** A lead is saved to the database first and sent afterwards (outbox). If the CRM is down, the bot retries after 1, 5, 30, 120 and 720 minutes.
- **Website forms.** `POST /api/leads` accepts leads from any site; a sample form is in `integrations/website_form.html`. Spam protection: honeypot field, per-IP rate limit and an allow-list of origins.

## Demo mode

With `DEMO_MODE=true` a visitor gets a sales-team copy of their own lead card with working buttons after submitting.

`/stats`, `/leads` and `/export` are available too: `/stats` shows only aggregate numbers, `/leads` and `/export` show only the visitor's own leads. Demo visitors never see other people's phone numbers.

## Customising the questions

No code changes needed — everything is in `funnel.json`:
- `steps` — questions in order. Types: `choice` (buttons), `text`, `phone`, `email`.
- Buttons have a `score`; text answers can score too: `"score": 5`.
- `"optional": true` adds a "Skip" button.
- `text` questions can also have `options` — quick-reply buttons (e.g. popular cities) while still allowing a typed answer.
- `"field": "name" | "phone" | "email"` puts the answer into its own column on the card and in the CSV.
- `scoring` — thresholds: from `hot` points the lead is hot, from `warm` it's warm.
- `finish` — the reply for each lead type; `{name}` is replaced with the prospect's name.
- `sla_minutes` — how fast a lead must be taken before the bot nudges the chat.

If the file has a mistake, the bot refuses to start and says what to fix, e.g. "step 2 (bill): a choice step needs at least 2 options".

## Google Sheets in 5 minutes

1. Create a spreadsheet, then **Extensions** → **Apps Script**. Paste the code from `integrations/google_sheets.gs`.
2. Replace `SECRET` with a long random string.
3. **Deploy** → **New deployment** → **Web app**, "Execute as: Me", "Who has access: Anyone".
4. Copy the URL, append `?key=YOUR_SECRET` and put it into `WEBHOOK_URLS` in `.env`.

## Run locally

1. Create a new bot in @BotFather.
2. In PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

3. Fill in `BOT_TOKEN` and `ADMIN_IDS` in `.env` (the bot's `/chatid` command shows your ID).
4. Start:

```powershell
.venv\Scripts\python -m bot
```

Ad link format: `https://t.me/BOT_NAME?start=campaign_name`.

> Telegram allows only one running copy per token — don't run the bot locally while it's running on a server.

## Deployment (Docker Compose)

```yaml
  leadcatcher-bot:
    build: ../leadcatcher-bot
    env_file: ../leadcatcher-bot/.env
    environment:
      DATABASE_URL: sqlite+aiosqlite:////app/data/leads.db
      TRUST_PROXY: "true"
    volumes:
      - leads-data:/app/data
    restart: unless-stopped
```

Website forms need the API behind HTTPS, e.g. with Caddy:

```
leads.example.com {
	reverse_proxy leadcatcher-bot:8080
}
```

## Tests

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

22 tests covering:
- **the funnel**: the full path from `/start` to submit, invalid input, back / skip, stale buttons, someone else's contact, double "Send";
- **lead scoring** and clear errors in `funnel.json`;
- **the sales team**: buttons and status changes, only one manager can take a lead, group and demo permissions, overdue hot-lead nudges, abandoned-funnel reminders, `/stats`, `/leads`, `/export`;
- **website forms**: field validation, honeypot, allowed origins, rate limiting;
- **webhooks** against a real local HTTP server: signatures, retries on CRM errors, giving up after 6 attempts, Apps Script `{"ok": false}` and crashed-script error pages.

## Project structure

```
bot/
  __main__.py      entry point: bot + form API + background checks
  config.py        settings from .env
  funnel.py        funnel from funnel.json, scoring, validation (pure logic)
  db.py            tables: leads, lead_cards, outbox
  repo.py          database queries, stats
  texts.py         texts and the lead card
  keyboards.py     buttons
  notify.py        cards in the sales chat
  watcher.py       deadline and abandoned-funnel reminders
  webhooks.py      delivery to Google Sheets / CRM with retries
  web.py           POST /api/leads for website forms
  export.py        CSV
  handlers/
    funnel_flow.py the questionnaire
    sales.py       card buttons
    admin.py       /stats /leads /export /chatid
integrations/      google_sheets.gs, website_form.html
funnel.json        demo company questions
tests/
```

## Tech stack

Python, aiogram 3, aiohttp, SQLAlchemy 2, HMAC-signed webhooks with an outbox and retries, Google Apps Script, Docker, pytest.

## License

[MIT](LICENSE)
