"""Leads from website forms."""
from bot import notify

FORM = {"name": "Mari Tamm", "phone": "+372 5123 4567", "email": "", "message": "Teeth whitening, please.\nAfter 17:00",
        "source": "landing-dental", "service": "Whitening", "company_website": ""}


async def test_website_lead_reaches_sales(make_env):
    env = await make_env()
    c = await env.client()
    res = await c.post("/api/leads", json=FORM, headers={"Origin": "https://smile.example"})
    data = await res.json()
    assert res.status == 200 and data == {"ok": True, "id": 1}
    assert res.headers["Access-Control-Allow-Origin"] == "https://smile.example"
    await notify.drain()
    lead = await env.repo.lead(1)
    assert (lead.channel, lead.source, lead.temperature, lead.status) == ("website", "landing-dental", "warm", "new")
    assert lead.answers == {"service": "Whitening", "Message": "Teeth whitening, please.\nAfter 17:00"}
    card = env.session.texts(99)[-1]
    assert "🌤 WARM <b>lead #1</b>" in card and "score" not in card and "🌐 Website" in card
    assert "<b>service:</b> Whitening" in card and "Mari Tamm" in card and "call within 120 min" in card

    res = await c.post("/api/leads", data={"name": "Jaan", "email": "jaan@example.ee"})  # a plain HTML form
    assert res.status == 200 and (await res.json())["id"] == 2
    assert (await c.get("/health")).status == 200
    await c.close()


async def test_validation_and_spam_protection(make_env):
    env = await make_env()
    env.limiter.limit = 100
    c = await env.client()
    cases = [({"name": "M"}, "your name"), ({"phone": "call me"}, "phone number"), ({"email": "x@"}, "email"),
             ({"phone": "", "email": ""}, "phone number or an email")]
    for change, error in cases:
        res = await c.post("/api/leads", json={**FORM, **change})
        assert res.status == 400 and error in (await res.json())["error"], change
    assert (await c.post("/api/leads", data="not json", headers={"Content-Type": "application/json"})).status == 400
    assert (await c.post("/api/leads", json=["a", "list"])).status == 400

    res = await c.post("/api/leads", json={**FORM, "company_website": "http://spam.example"})  # a bot filled the trap
    assert res.status == 200 and await res.json() == {"ok": True}
    assert await env.repo.all_leads() == []
    await c.close()


async def test_allowed_websites_and_rate_limit(make_env):
    env = await make_env(allowed_origins=("https://smile.example/",))
    c = await env.client()
    res = await c.options("/api/leads", headers={"Origin": "https://smile.example"})
    assert res.status == 204 and res.headers["Access-Control-Allow-Methods"] == "POST, OPTIONS"
    assert (await c.options("/api/leads", headers={"Origin": "https://evil.example"})).status == 403
    res = await c.post("/api/leads", json=FORM, headers={"Origin": "https://evil.example"})
    assert res.status == 403

    statuses = [(await c.post("/api/leads", json=FORM, headers={"Origin": "https://smile.example"})).status
                for _ in range(6)]
    assert statuses == [200] * 5 + [429]
    await c.close()
    await notify.drain()
