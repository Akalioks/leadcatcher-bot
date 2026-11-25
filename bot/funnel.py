"""The questionnaire. It is loaded from funnel.json, so questions, answers and scores change without touching code.
Everything here is pure logic (no Telegram, no database), which makes it easy to test."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

TYPES = {"choice", "text", "phone", "email"}
FIELDS = {"name", "phone", "email"}  # answers that also go into the lead's own columns
TEMPERATURES = ("hot", "warm", "cold")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_CHARS_RE = re.compile(r"^\+?[0-9 ()\-.]+$")


class FunnelError(ValueError):
    """funnel.json is invalid. The message says what to fix."""


@dataclass(frozen=True)
class Option:
    label: str
    score: int = 0


@dataclass(frozen=True)
class Step:
    id: str
    type: str
    question: str
    title: str
    options: tuple[Option, ...] = ()  # choice: the only answers; text: quick-answer buttons, typing works too
    optional: bool = False
    score: int = 0       # points for answering a text / phone / email step
    min_len: int = 1
    max_len: int = 200
    field: str | None = None


@dataclass(frozen=True)
class Funnel:
    company: str
    intro: str
    steps: tuple[Step, ...]
    hot: int
    warm: int
    finish: dict[str, str]
    sla_minutes: dict[str, int]
    website_temperature: str = "warm"

    @property
    def by_id(self) -> dict[str, Step]:
        return {s.id: s for s in self.steps}

    def title(self, key: str) -> str:
        step = self.by_id.get(key)
        return step.title if step else key

    def score(self, answers: dict[str, str]) -> int:
        total = 0
        for step in self.steps:
            value = answers.get(step.id, "")
            if not value:
                continue
            if step.type == "choice":
                total += next((o.score for o in step.options if o.label == value), 0)
            else:
                total += step.score
        return max(0, min(100, total))

    def temperature(self, score: int) -> str:
        return "hot" if score >= self.hot else "warm" if score >= self.warm else "cold"


def validate_answer(step: Step, raw: str) -> tuple[str | None, str | None]:
    """Returns (clean value, None) or (None, a friendly error for the user)."""
    text = " ".join((raw or "").split())
    if step.type == "phone":
        digits = re.sub(r"\D", "", text)
        if PHONE_CHARS_RE.match(text) and 7 <= len(digits) <= 15:
            return text, None
        return None, "That doesn't look like a phone number. Example: <b>+372 5555 1234</b>"
    if step.type == "email":
        if EMAIL_RE.match(text) and len(text) <= 120:
            return text.lower(), None
        return None, "That doesn't look like an email. Example: <b>name@example.com</b>"
    if len(text) < step.min_len:
        return None, f"Please write at least {step.min_len} characters."
    if len(text) > step.max_len:
        return None, f"That's a bit long. Please keep it under {step.max_len} characters."
    return text, None


def parse_funnel(data: dict) -> Funnel:
    try:
        steps = []
        for i, s in enumerate(data["steps"], 1):
            where = f"step {i} ({s.get('id', '?')})"
            if s.get("type") not in TYPES:
                raise FunnelError(f"{where}: type must be one of {sorted(TYPES)}")
            options = tuple(Option(label=o["label"], score=int(o.get("score", 0))) for o in s.get("options", []))
            if s["type"] == "choice" and len(options) < 2:
                raise FunnelError(f"{where}: a choice step needs at least 2 options")
            if options and s["type"] not in ("choice", "text"):
                raise FunnelError(f"{where}: only choice and text steps can have options")
            if s.get("field") not in (None, *FIELDS):
                raise FunnelError(f"{where}: field must be one of {sorted(FIELDS)}")
            if any(len(o.label.encode()) > 60 for o in options):
                raise FunnelError(f"{where}: option labels must be short (max ~60 characters)")
            steps.append(Step(
                id=s["id"], type=s["type"], question=s["question"], title=s.get("title", s["id"]),
                options=options, optional=bool(s.get("optional", False)), score=int(s.get("score", 0)),
                min_len=int(s.get("min_len", 1)), max_len=int(s.get("max_len", 200)), field=s.get("field"),
            ))
        if not steps:
            raise FunnelError("the funnel has no steps")
        if len({s.id for s in steps}) != len(steps):
            raise FunnelError("step ids must be unique")
        finish = data["finish"]
        if set(finish) != set(TEMPERATURES):
            raise FunnelError(f"finish must have texts for {list(TEMPERATURES)}")
        temp = data.get("website_temperature", "warm")
        if temp not in TEMPERATURES:
            raise FunnelError(f"website_temperature must be one of {list(TEMPERATURES)}")
        return Funnel(
            company=data["company"], intro=data["intro"], steps=tuple(steps),
            hot=int(data["scoring"]["hot"]), warm=int(data["scoring"]["warm"]), finish=dict(finish),
            sla_minutes={k: int(v) for k, v in data.get("sla_minutes", {}).items()}, website_temperature=temp,
        )
    except KeyError as e:
        raise FunnelError(f"missing key {e}") from None


def load_funnel(path: str | Path) -> Funnel:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FunnelError(f"{path} is not valid JSON: {e}") from None
    return parse_funnel(data)
