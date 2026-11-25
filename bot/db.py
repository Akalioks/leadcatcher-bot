"""Database tables. SQLite by default, PostgreSQL works by changing DATABASE_URL."""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# draft (filling in the questionnaire) → new → taken → contacted → won | lost
STATUSES = ("draft", "new", "taken", "contacted", "won", "lost")
TRANSITIONS = {
    "new": {"taken", "lost"},
    "taken": {"contacted", "won", "lost"},
    "contacted": {"won", "lost"},
}


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), default="telegram")  # telegram | website
    source: Mapped[str] = mapped_column(String(64), default="")  # ad campaign from the t.me/bot?start=... link
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    step: Mapped[int] = mapped_column(default=0)  # current question while the lead is a draft
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # step id (or form field) → answer
    score: Mapped[int] = mapped_column(default=0)
    temperature: Mapped[str] = mapped_column(String(8), default="")  # hot | warm | cold
    name: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    manager_id: Mapped[int | None] = mapped_column(BigInteger)
    manager_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    completed_at: Mapped[datetime | None] = mapped_column(index=True)
    taken_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    nudged_at: Mapped[datetime | None]
    sla_alerted_at: Mapped[datetime | None]

    cards: Mapped[list["LeadCard"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class LeadCard(Base):
    """A message with the lead card in the sales chat. Kept so every copy is updated when the status changes."""
    __tablename__ = "lead_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int]
    demo_copy: Mapped[bool] = mapped_column(default=False)  # the copy a demo visitor gets


class Outbox(Base):
    """Webhook deliveries. Stored first and sent after, so nothing is lost if the CRM is down or the bot restarts."""
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    next_at: Mapped[datetime] = mapped_column(index=True)
    delivered_at: Mapped[datetime | None]
    failed: Mapped[bool] = mapped_column(default=False)  # gave up after the last attempt
    last_error: Mapped[str] = mapped_column(String(300), default="")
