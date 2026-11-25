"""Leads → CSV for Excel / Google Sheets."""
import csv
import io

from .db import Lead
from .funnel import Funnel


def to_csv(leads: list[Lead], funnel: Funnel) -> bytes:
    question_cols = [s for s in funnel.steps if not s.field]  # name/phone/email have their own columns
    known = {s.id for s in funnel.steps}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID", "Date", "Channel", "Source", "Temperature", "Score", "Status", "Name", "Phone", "Email",
                "Telegram", "Manager", *[s.title for s in question_cols], "Other"])
    for l in leads:
        other = "; ".join(f"{k}: {v}" for k, v in l.answers.items() if k not in known and v)
        w.writerow([l.id, l.completed_at.strftime("%Y-%m-%d %H:%M") if l.completed_at else "", l.channel,
                    l.source or "direct", l.temperature, l.score, l.status, l.name, l.phone, l.email,
                    f"@{l.username}" if l.username else "", l.manager_name,
                    *[l.answers.get(s.id, "") for s in question_cols], other])
    return out.getvalue().encode("utf-8-sig")  # the BOM makes Excel read non-English letters correctly
