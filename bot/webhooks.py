"""Sends every lead (and every status change) to Google Sheets, Zapier, Make, n8n or a CRM.

Deliveries are written to the database first (the "outbox") and then sent, with retries.
So a lead is never lost if the CRM is down for an hour or the bot restarts."""
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import timedelta

import aiohttp

from .config import Config
from .db import Lead
from .funnel import Funnel
from .repo import Repo

log = logging.getLogger(__name__)
RETRY_AFTER_MIN = (1, 5, 30, 120, 720)  # wait between attempts; after the last one we give up and log it


def payload(event: str, lead: Lead, funnel: Funnel) -> dict:
    return {
        "event": event,  # lead.created | lead.updated
        "lead": {
            "id": lead.id,
            "created_at": (lead.completed_at or lead.created_at).isoformat(timespec="seconds"),
            "channel": lead.channel,
            "source": lead.source or "direct",
            "status": lead.status,
            "temperature": lead.temperature,
            "score": lead.score,
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "telegram": f"@{lead.username}" if lead.username else "",
            "manager": lead.manager_name,
            "answers": {funnel.title(k): v for k, v in lead.answers.items() if v},
        },
    }


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Webhooks:
    def __init__(self, cfg: Config, repo: Repo, timeout_sec: float = 10):
        self.cfg, self.repo = cfg, repo
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._http: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def enqueue(self, event: str, lead: Lead, funnel: Funnel) -> None:
        if not self.cfg.webhook_urls:
            return
        body = json.dumps(payload(event, lead, funnel), ensure_ascii=False)
        await self.repo.enqueue(self.cfg.webhook_urls, body)

    async def deliver_due(self) -> int:
        """Sends everything that is due. Returns how many were delivered."""
        async with self._lock:  # the loop and an instant send never deliver the same item twice
            delivered = 0
            for item in await self.repo.due_outbox():
                error = await self._post(item.url, item.body)
                if error and item.attempts < len(RETRY_AFTER_MIN):
                    next_at = self.cfg.now() + timedelta(minutes=RETRY_AFTER_MIN[item.attempts])
                    log.warning("Webhook to %s failed (%s), retry at %s", _host(item.url), error, next_at)
                elif error:
                    next_at = None
                    log.error("Webhook to %s failed %d times, giving up: %s", _host(item.url), item.attempts + 1, error)
                else:
                    next_at, delivered = None, delivered + 1
                await self.repo.outbox_result(item.id, error, next_at)
            return delivered

    async def _post(self, url: str, body: str) -> str:
        """Returns "" on success, otherwise a short error."""
        data = body.encode()
        headers = {"Content-Type": "application/json"}
        if self.cfg.webhook_secret:
            headers["X-Signature"] = sign(self.cfg.webhook_secret, data)
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=self.timeout)
        try:
            async with self._http.post(url, data=data, headers=headers) as r:
                text = await r.text()
                if r.status >= 300:
                    return f"HTTP {r.status}: {text[:150]}"
                try:  # Google Apps Script always answers 200, so it reports errors as {"ok": false}
                    reply = json.loads(text)
                except ValueError:
                    reply = None
                if isinstance(reply, dict) and reply.get("ok") is False:
                    return f"rejected: {str(reply.get('error', text))[:150]}"
                if "script.google.com" in url and not (isinstance(reply, dict) and reply.get("ok") is True):
                    # a crashed script answers 200 with an HTML error page: that is not a delivery
                    return f"Apps Script did not confirm: {' '.join(text.split())[:120]}"
                return ""
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return f"{type(e).__name__}: {e}"[:200]

    async def run(self, every_sec: float = 5) -> None:
        while True:
            try:
                await self.deliver_due()
            except Exception:
                log.exception("Webhook loop error")
            await asyncio.sleep(every_sec)

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()


def _host(url: str) -> str:
    """For logs: the address without the path, which may contain a secret key."""
    return url.split("/")[2] if "//" in url else url
