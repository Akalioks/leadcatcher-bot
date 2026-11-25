"""All database queries live here."""
from collections import Counter
from datetime import datetime, timedelta

from aiogram.types import User
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .config import Config
from .db import TRANSITIONS, Base, Lead, LeadCard, Outbox
from .funnel import Funnel


class Repo:
    def __init__(self, engine: AsyncEngine, cfg: Config):
        self.engine = engine
        self.cfg = cfg
        self.session = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    async def create(cls, cfg: Config) -> "Repo":
        engine = create_async_engine(cfg.database_url)
        if engine.dialect.name == "sqlite":
            event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return cls(engine, cfg)

    async def close(self) -> None:
        await self.engine.dispose()

    # ---------- questionnaire ----------
    async def start_draft(self, user: User, source: str) -> Lead:
        """One unfinished questionnaire per person: /start again begins it from the first question."""
        now = self.cfg.now()
        async with self.session() as s:
            lead = await s.scalar(select(Lead).where(Lead.user_id == user.id, Lead.status == "draft"))
            if lead is None:
                lead = Lead(user_id=user.id, channel="telegram", status="draft")
                s.add(lead)
            lead.source, lead.username, lead.first_name = source, user.username, user.first_name[:64]
            lead.step, lead.answers, lead.created_at, lead.updated_at, lead.nudged_at = 0, {}, now, now, None
            await s.commit()
            return lead

    async def draft_of(self, user_id: int) -> Lead | None:
        async with self.session() as s:
            return await s.scalar(select(Lead).where(Lead.user_id == user_id, Lead.status == "draft"))

    async def lead(self, lead_id: int) -> Lead | None:
        async with self.session() as s:
            return await s.get(Lead, lead_id)

    async def update_draft(self, lead_id: int, answers: dict, step: int) -> Lead:
        async with self.session() as s:
            lead = await s.get(Lead, lead_id)
            lead.answers, lead.step, lead.updated_at = dict(answers), step, self.cfg.now()
            await s.commit()
            return lead

    async def complete(self, lead_id: int, funnel: Funnel) -> Lead | None:
        """Turns a finished draft into a lead. None if it was already sent (double tap)."""
        async with self.session() as s:
            lead = await s.get(Lead, lead_id)
            if lead is None or lead.status != "draft" or lead.step < len(funnel.steps):
                return None
            for step in funnel.steps:
                if step.field:
                    setattr(lead, step.field, lead.answers.get(step.id, ""))
            lead.name = lead.name or lead.first_name
            lead.score = funnel.score(lead.answers)
            lead.temperature = funnel.temperature(lead.score)
            lead.status, lead.completed_at = "new", self.cfg.now()
            lead.updated_at = lead.completed_at
            await s.commit()
            return lead

    async def create_web_lead(self, *, name: str, phone: str, email: str, source: str,
                              answers: dict, temperature: str) -> Lead:
        now = self.cfg.now()
        async with self.session() as s:
            lead = Lead(channel="website", source=source, name=name, first_name=name[:64], phone=phone, email=email,
                        answers=answers, status="new", temperature=temperature, created_at=now, updated_at=now,
                        completed_at=now)
            s.add(lead)
            await s.commit()
            return lead

    # ---------- sales team ----------
    async def add_card(self, lead_id: int, chat_id: int, message_id: int, demo_copy: bool = False) -> None:
        async with self.session() as s:
            s.add(LeadCard(lead_id=lead_id, chat_id=chat_id, message_id=message_id, demo_copy=demo_copy))
            await s.commit()

    async def set_status(self, lead_id: int, status: str, manager_id: int,
                         manager_name: str) -> tuple[Lead | None, str]:
        """Returns (lead, "") or (lead or None, the reason it isn't allowed)."""
        async with self.session() as s:
            lead = await s.get(Lead, lead_id)
            if lead is None:
                return None, "Lead not found."
            if status not in TRANSITIONS.get(lead.status, set()):
                return lead, f"This lead is already {lead.status}."
            now = self.cfg.now()
            lead.status, lead.updated_at = status, now
            if status == "taken":
                lead.manager_id, lead.manager_name, lead.taken_at = manager_id, manager_name[:64], now
            if status in ("won", "lost"):
                lead.closed_at = now
                lead.manager_id = lead.manager_id or manager_id
                lead.manager_name = lead.manager_name or manager_name[:64]
            await s.commit()
            return lead, ""

    async def recent(self, user_id: int | None = None, limit: int = 10) -> list[Lead]:
        async with self.session() as s:
            q = select(Lead).where(Lead.status != "draft")
            if user_id is not None:
                q = q.where(Lead.user_id == user_id)
            return list((await s.scalars(q.order_by(Lead.id.desc()).limit(limit))).all())

    async def all_leads(self, user_id: int | None = None) -> list[Lead]:
        return list(reversed(await self.recent(user_id, limit=100_000)))

    async def stats(self, days: int = 7) -> dict:
        since = self.cfg.now() - timedelta(days=days)
        today = self.cfg.now().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.session() as s:
            leads = list((await s.scalars(select(Lead).where(Lead.created_at >= since))).all())
        done = [l for l in leads if l.status != "draft"]
        tg = [l for l in leads if l.channel == "telegram"]
        tg_done = [l for l in tg if l.status != "draft"]
        waits = [(l.taken_at - l.completed_at).total_seconds() / 60 for l in done if l.taken_at and l.completed_at]
        return {
            "days": days,
            "started": len(tg),
            "completed": len(done),
            "conversion": round(100 * len(tg_done) / len(tg)) if tg else 0,
            "temperature": Counter(l.temperature for l in done),
            "status": Counter(l.status for l in done),
            "sources": Counter(l.source or "direct" for l in done).most_common(5),
            "avg_take_min": round(sum(waits) / len(waits)) if waits else None,
            "today": sum(1 for l in done if l.completed_at and l.completed_at >= today),
        }

    # ---------- background checks ----------
    async def due_sla(self, sla_minutes: dict[str, int]) -> list[Lead]:
        """New leads nobody took in time."""
        now = self.cfg.now()
        async with self.session() as s:
            q = select(Lead).where(Lead.status == "new", Lead.sla_alerted_at.is_(None))
            leads = (await s.scalars(q)).all()
        return [l for l in leads if l.temperature in sla_minutes and l.completed_at
                and now - l.completed_at >= timedelta(minutes=sla_minutes[l.temperature])]

    async def due_nudges(self, after_min: int) -> list[Lead]:
        """People who answered something, then went quiet. Reminded once, only within a day."""
        now = self.cfg.now()
        async with self.session() as s:
            q = select(Lead).where(Lead.status == "draft", Lead.step > 0, Lead.nudged_at.is_(None),
                                   Lead.updated_at <= now - timedelta(minutes=after_min),
                                   Lead.updated_at >= now - timedelta(hours=24))
            return list((await s.scalars(q)).all())

    async def mark(self, lead_id: int, **fields: datetime) -> None:
        async with self.session() as s:
            lead = await s.get(Lead, lead_id)
            for k, v in fields.items():
                setattr(lead, k, v)
            await s.commit()

    # ---------- webhooks ----------
    async def enqueue(self, urls: tuple[str, ...], body: str) -> None:
        async with self.session() as s:
            s.add_all(Outbox(url=u, body=body, next_at=self.cfg.now()) for u in urls)
            await s.commit()

    async def due_outbox(self, limit: int = 20) -> list[Outbox]:
        async with self.session() as s:
            q = (select(Outbox).where(Outbox.delivered_at.is_(None), Outbox.failed.is_(False),
                                      Outbox.next_at <= self.cfg.now()).order_by(Outbox.id).limit(limit))
            return list((await s.scalars(q)).all())

    async def outbox_result(self, item_id: int, error: str, next_at: datetime | None) -> None:
        """error == "" → delivered. Otherwise retry at next_at, or give up when next_at is None."""
        async with self.session() as s:
            item = await s.get(Outbox, item_id)
            item.attempts += 1
            if not error:
                item.delivered_at = self.cfg.now()
            else:
                item.last_error = error[:300]
                if next_at is None:
                    item.failed = True
                else:
                    item.next_at = next_at
            await s.commit()

    async def outbox_items(self) -> list[Outbox]:
        async with self.session() as s:
            return list((await s.scalars(select(Outbox).order_by(Outbox.id))).all())


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    """WAL: the bot and the web form can read while something is being written, and saves are much faster."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
