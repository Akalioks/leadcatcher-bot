"""The questionnaire file, scoring and answer checks (pure logic)."""
import json

import pytest

from bot.funnel import FunnelError, Step, load_funnel, parse_funnel, validate_answer
from tests.conftest import FUNNEL


def test_demo_funnel_loads_and_scores():
    f = load_funnel(FUNNEL)
    assert [s.id for s in f.steps] == ["property", "bill", "timeline", "city", "name", "phone", "email"]
    best = {"property": "🏠 Private house", "bill": "Over €300", "timeline": "🔥 Within a month", "email": "a@b.ee"}
    assert f.score(best) == 95 and f.temperature(95) == "hot"
    assert f.score({**best, "email": ""}) == 90  # skipped optional question gives no points
    assert f.score({"property": "🤔 Just exploring", "bill": "Under €50"}) == 0
    assert f.temperature(70) == "hot" and f.temperature(69) == "warm" and f.temperature(40) == "warm"
    assert f.temperature(39) == "cold"
    assert f.score({"property": "an answer that isn't in the list"}) == 0


def test_answer_validation():
    phone = Step(id="p", type="phone", question="", title="")
    assert validate_answer(phone, " +372  5555-1234 ") == ("+372 5555-1234", None)
    for bad in ("call me", "123", "+372 5555 1234 5555 1234", "5555@1234"):
        assert validate_answer(phone, bad)[1], bad
    email = Step(id="e", type="email", question="", title="")
    assert validate_answer(email, "Anna@Example.EE") == ("anna@example.ee", None)
    assert validate_answer(email, "anna@example")[1]
    text = Step(id="t", type="text", question="", title="", min_len=2, max_len=5)
    assert "at least 2" in validate_answer(text, "a")[1]
    assert "under 5" in validate_answer(text, "abcdef")[1]
    assert validate_answer(text, "  ab  ") == ("ab", None)


def test_broken_funnel_files_give_clear_errors(tmp_path):
    good = json.loads(FUNNEL.read_text(encoding="utf-8"))

    def broken(change) -> str:
        data = json.loads(json.dumps(good))
        change(data)
        with pytest.raises(FunnelError) as e:
            parse_funnel(data)
        return str(e.value)

    assert "type must be" in broken(lambda d: d["steps"][0].update(type="slider"))
    assert "at least 2 options" in broken(lambda d: d["steps"][0].update(options=[{"label": "Only"}]))
    assert "unique" in broken(lambda d: d["steps"][1].update(id="property"))
    assert "finish" in broken(lambda d: d["finish"].pop("cold"))
    assert "missing key" in broken(lambda d: d.pop("company"))
    assert "field must be" in broken(lambda d: d["steps"][3].update(field="address"))
    assert "can have options" in broken(lambda d: d["steps"][5].update(options=[{"label": "+372"}]))
    path = tmp_path / "f.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(FunnelError, match="not valid JSON"):
        load_funnel(path)
