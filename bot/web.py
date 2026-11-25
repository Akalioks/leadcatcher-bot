"""Leads from website forms: POST /api/leads. They land in the same sales chat, CSV and CRM as Telegram leads.

    fetch("https://leads.example.com/api/leads", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({name, phone, email, message, source: "landing-dental"})})

Protection against spam: an invisible "company_website" field (bots fill it in, people don't),
a limit per visitor, and a list of websites allowed to send forms (ALLOWED_ORIGINS)."""
import logging
import time
from collections import defaultdict, deque

from aiogram import Bot
from aiohttp import web

from . import notify
from .config import Config
from .funnel import Funnel, Step, validate_answer
from .handlers.funnel_flow import clean_source
from .repo import Repo
from .webhooks import Webhooks

log = logging.getLogger(__name__)
HONEYPOT = "company_website"
KNOWN = {"name", "phone", "email", "message", "source", HONEYPOT}
MAX_EXTRA_FIELDS = 10
_PHONE, _EMAIL = Step(id="phone", type="phone", question="", title=""), Step(id="email", type="email", question="", title="")


class RateLimiter:
    def __init__(self, limit: int = 5, per_sec: float = 600):
        self.limit, self.per_sec = limit, per_sec
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.per_sec:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 10_000:  # don't grow forever
            self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v})
        return True


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def create_web_app(cfg: Config, repo: Repo, funnel: Funnel, hooks: Webhooks, bot: Bot,
                   limiter: RateLimiter | None = None) -> web.Application:
    limiter = limiter or RateLimiter()
    allowed = {o.rstrip("/") for o in cfg.allowed_origins}

    def cors(origin: str | None) -> dict | None:
        """CORS headers for the browser; None if this website isn't allowed to send forms."""
        if not origin:
            return {}  # not a browser (curl, a server, Zapier) — allowed
        if "*" in allowed or origin.rstrip("/") in allowed:
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400"}
        return None

    def reply(status: int, data: dict, headers: dict) -> web.Response:
        return web.json_response(data, status=status, headers=headers)

    def client_ip(request: web.Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if cfg.trust_proxy and forwarded:
            return forwarded.split(",")[-1].strip()  # the address our own proxy (Caddy) saw
        return request.remote or "?"

    async def preflight(request: web.Request) -> web.Response:
        headers = cors(request.headers.get("Origin"))
        return web.Response(status=204 if headers is not None else 403, headers=headers or {})

    async def create_lead(request: web.Request) -> web.Response:
        headers = cors(request.headers.get("Origin"))
        if headers is None:
            return reply(403, {"ok": False, "error": "This website is not allowed to send leads."}, {})
        if not limiter.allow(client_ip(request)):
            return reply(429, {"ok": False, "error": "Too many requests. Please try again in a few minutes."}, headers)
        try:
            data = await request.json() if request.content_type == "application/json" else dict(await request.post())
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return reply(400, {"ok": False, "error": "Invalid request."}, headers)
        if _text(data.get(HONEYPOT), 10):
            log.info("Website form: spam bot filtered out (honeypot)")
            return reply(200, {"ok": True}, headers)  # the bot thinks it worked

        name = _text(data.get("name"), 80)
        phone_raw, email_raw = _text(data.get("phone"), 40), _text(data.get("email"), 120)
        phone, phone_err = validate_answer(_PHONE, phone_raw) if phone_raw else ("", None)
        email, email_err = validate_answer(_EMAIL, email_raw) if email_raw else ("", None)
        error = ("Please enter your name." if len(name) < 2 else
                 "Please check the phone number." if phone_err else
                 "Please check the email address." if email_err else
                 "Please enter a phone number or an email." if not (phone or email) else "")
        if error:
            return reply(400, {"ok": False, "error": error}, headers)

        answers = {}
        for key, value in data.items():  # any extra form fields: "service", "date", "budget"...
            if key not in KNOWN and isinstance(value, (str, int, float)) and len(answers) < MAX_EXTRA_FIELDS:
                if text := _text(value, 300):
                    answers[_text(key, 40)] = text
        if message := str(data.get("message") or "").strip()[:1000]:
            answers["Message"] = message

        lead = await repo.create_web_lead(name=name, phone=phone or "", email=email or "",
                                          source=clean_source(str(data.get("source") or "website")) or "website",
                                          answers=answers, temperature=funnel.website_temperature)
        notify.in_background(notify.new_lead(bot, cfg, repo, funnel, lead))
        await hooks.enqueue("lead.created", lead, funnel)
        notify.in_background(hooks.deliver_due())
        return reply(200, {"ok": True, "id": lead.id}, headers)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/api/leads", create_lead)
    app.router.add_route("OPTIONS", "/api/leads", preflight)
    return app
