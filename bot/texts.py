"""All texts the bot sends. Everything typed by people is escaped, because messages use HTML formatting."""
from datetime import datetime
from html import escape

from .db import Lead
from .funnel import Funnel, Step

BACK = "⬅️ Back"
SKIP = "Skip ➡️"
SHARE_PHONE = "📱 Share my phone number"

TEMP = {"hot": "🔥 HOT", "warm": "🌤 WARM", "cold": "❄️ COLD"}
STATUS = {"new": "🆕 New", "taken": "🙋 Taken", "contacted": "📞 Contacted", "won": "✅ Won", "lost": "❌ Lost",
          "draft": "✏️ Filling in"}

NO_DRAFT = "Send /start to leave a request. It takes about a minute 🙂"
PICK_BUTTON = "Please pick one of the options above 👆"
TAP_SEND = "Please check your answers and tap <b>✅ Send request</b>."
OWN_PHONE = "Please share <b>your own</b> number or type it."
TEAM_ONLY = "This command is for the sales team."


TYPE_HINT = "✍️ <i>Type your answer in the message box below.</i>"
TAP_OR_TYPE = "<i>Tap a button or type your own answer.</i>"


def question(funnel: Funnel, index: int, with_hint: bool = True) -> str:
    step = funnel.steps[index]
    text = f"<b>{index + 1}/{len(funnel.steps)}</b> · {step.question}"
    if with_hint and step.type in ("text", "email"):  # otherwise people wait for buttons that never come
        text += "\n\n" + (TAP_OR_TYPE if step.options else TYPE_HINT)
    return text


def answered(funnel: Funnel, index: int, value: str) -> str:
    return f"{question(funnel, index, with_hint=False)}\n\n✅ <b>{escape(value)}</b>"


def _answer_lines(lead: Lead, funnel: Funnel, skip_fields: bool) -> list[str]:
    fields = {s.id for s in funnel.steps if s.field} if skip_fields else set()
    lines = []
    for key, value in lead.answers.items():
        if key in fields or not value:
            continue
        lines.append(f"<b>{escape(funnel.title(key))}:</b> {escape(str(value))}")
    return lines


def summary(lead: Lead, funnel: Funnel) -> str:
    lines = []
    for step in funnel.steps:
        value = lead.answers.get(step.id) or "—"
        lines.append(f"• <b>{escape(step.title)}:</b> {escape(value)}")
    return "📝 <b>Please check your request:</b>\n\n" + "\n".join(lines)


def finish(lead: Lead, funnel: Funnel) -> str:
    return funnel.finish[lead.temperature].format(name=escape(lead.name or lead.first_name or "there"))


def intro(funnel: Funnel, first_name: str) -> str:
    return funnel.intro.format(first_name=escape(first_name))


def _when(dt: datetime | None) -> str:
    return dt.strftime("%d.%m %H:%M") if dt else "—"


def _minutes(a: datetime | None, b: datetime | None) -> str:
    if not (a and b):
        return ""
    m = max(0, int((b - a).total_seconds() // 60))
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min"


def card(lead: Lead, funnel: Funnel) -> str:
    """The lead as the sales team sees it."""
    head = f"{TEMP.get(lead.temperature, '🌤')} <b>lead #{lead.id}</b>"
    if lead.channel == "telegram":
        head += f" · score {lead.score}/100"
    who = escape(lead.name or lead.first_name or "—")
    if lead.username:
        who += f" (@{escape(lead.username)})"
    lines = [head, "", f"👤 {who}"]
    if lead.phone:
        lines.append(f"📞 {escape(lead.phone)}")
    if lead.email:
        lines.append(f"✉️ {escape(lead.email)}")
    answers = _answer_lines(lead, funnel, skip_fields=lead.channel == "telegram")
    if answers:
        lines += [""] + answers
    channel = "Telegram" if lead.channel == "telegram" else "🌐 Website"
    lines += ["", f"📣 Source: {escape(lead.source or 'direct')} · {channel}", f"🕐 {_when(lead.completed_at)}"]

    status = STATUS.get(lead.status, lead.status)
    if lead.status == "new":
        sla = funnel.sla_minutes.get(lead.temperature)
        status += f" — call within {sla} min" if sla else ""
    elif lead.manager_name:
        status += f" by {escape(lead.manager_name)}"
        if lead.status == "taken":
            status += f" ({_minutes(lead.completed_at, lead.taken_at)} after arrival)"
    lines.append(f"<b>Status:</b> {status}")
    return "\n".join(lines)


def demo_card(text: str) -> str:
    return "👀 <i>Demo: this is what the sales team receives. Try the buttons:</i>\n\n" + text


def sla_alert(lead: Lead, minutes: int) -> str:
    return (f"⏰ <b>{TEMP.get(lead.temperature, '')} lead #{lead.id} has been waiting {minutes} min.</b>\n"
            f"Nobody has taken it yet. Hot leads go cold fast: please call {escape(lead.name)} now.")


def nudge(lead: Lead, funnel: Funnel) -> str:
    left = len(funnel.steps) - lead.step
    return (f"👋 You were just <b>{left} question{'s' if left != 1 else ''}</b> away from your free estimate.\n"
            "Want to finish? It takes less than a minute.")


def taken_for_client(lead: Lead, funnel: Funnel) -> str:
    return (f"👋 <b>{escape(lead.manager_name)}</b> from {escape(funnel.company)} has your request "
            "and will contact you shortly.")


def lead_line(lead: Lead) -> str:
    return (f"#{lead.id} {TEMP.get(lead.temperature, '')} · {escape(lead.name or '—')} · "
            f"{STATUS.get(lead.status, lead.status)} · {_when(lead.completed_at)}")


def stats(st: dict) -> str:
    t, s = st["temperature"], st["status"]
    lines = [
        f"📊 <b>Leads — last {st['days']} days</b>",
        "",
        f"Started the questionnaire: <b>{st['started']}</b>",
        f"Leads received: <b>{st['completed']}</b> (Telegram completion rate {st['conversion']}%)",
        f"🔥 Hot {t.get('hot', 0)} · 🌤 Warm {t.get('warm', 0)} · ❄️ Cold {t.get('cold', 0)}",
        f"✅ Won {s.get('won', 0)} · ❌ Lost {s.get('lost', 0)} · 🆕 Waiting {s.get('new', 0)}",
    ]
    if st["avg_take_min"] is not None:
        lines.append(f"⏱ Average time until a manager takes a lead: <b>{st['avg_take_min']} min</b>")
    if st["sources"]:
        lines.append("📣 Top sources: " + ", ".join(f"{escape(k)} {v}" for k, v in st["sources"]))
    lines += ["", f"Today: <b>{st['today']}</b> new lead(s)"]
    return "\n".join(lines)


def step_hint(step: Step) -> str:
    return {"phone": "Type a phone number or tap the button below.",
            "email": "Type an email address.",
            "text": "Type your answer."}.get(step.type, PICK_BUTTON)
