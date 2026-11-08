"""`POST /v1/apps/{app}/sources/suggest` (#152, Builder "Add a source"). Recorded fixtures, no network."""

import json

from fake_llm import FakeAsyncOpenAI
from fastapi.testclient import TestClient

from app.agents.llm import LlmRouter
from app.agents.source_suggester import catalogue
from app.core.config import Settings
from app.core.db import Db
from app.main import create_app

INTENT = "show where items are reported in the area"
PUSH_DESC = "readings our own app sends whenever a device reports a value"
PULL_DESC = "a public feed of local reports at https://feeds.example.org/area.xml"


def _router(script) -> tuple[LlmRouter, FakeAsyncOpenAI]:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake}), fake


def _script(*contents: str) -> dict:
    return {"scenario": "inline", "description": "d", "steps": [{"response": {"content": c}} for c in contents]}


def _post(script, description: str, *, empty_budget: bool = False):
    router, fake = _router(script)
    app = create_app(db=Db(":memory:"), llm=router, autostart_engine=False)
    if empty_budget:
        for p in ("nvidia", "openrouter"):
            router._provider(p).bucket.tokens = 0.0
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/sources/suggest", json={"intent": INTENT, "description": description})
    return r, fake, app.state.db


def _types(body) -> list[tuple[str, str | None]]:
    return [(o["type"], (o.get("config") or {}).get("preset")) for o in body["options"]]


def _is_unranked_fallback(body) -> bool:
    return _types(body) == [(o.type, (o.config or {}).get("preset")) for o in catalogue()]


def test_push_source_is_ranked_from_the_recorded_answer():
    r, fake, db = _post("source_suggest_push", PUSH_DESC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["shape"]) == ("push", "point")
    assert [o["type"] for o in body["options"]] == ["post_api", "webhook"]
    assert body["options"][0]["label"] == "POST API"  # labels come from code, not the model
    assert body["meaning"] == PUSH_DESC
    assert all(o.get("config") is None for o in body["options"])
    # one structured, low-effort call on the extract (Lightning) model, reasoning off
    (call,) = fake.calls
    assert call["model"] == Settings(_env_file=None).agent_model("extract").model_id
    assert call["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False, "low_effort": True}
    assert call["response_format"] == {"type": "json_object"}
    assert any(t.action == "suggest_source" and t.why == "validated" for t in db.list_trace("some-app"))


def test_pull_source_keeps_the_devs_url_drops_an_invented_one_and_lists_the_rest_of_its_kind():
    r, _, _ = _post("source_suggest_pull", PULL_DESC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["shape"]) == ("pull", "document")
    assert body["meaning"] == PULL_DESC
    first, second, *rest = body["options"]
    assert first["type"] == "rss" and first["config"] == {"url": "https://feeds.example.org/area.xml"}
    assert second["type"] == "json_api" and second.get("config") is None  # URL not in the dev's words
    assert {o["type"] for o in body["options"]} <= {"rss", "json_api", "json_list", "local_file"}  # pull options only
    assert any((o.get("config") or {}).get("preset") for o in rest)  # presets still offered after the ranked ones


def test_invalid_answer_takes_the_repair_path():
    good = json.dumps({"kind": "pull", "shape": "field", "options": [{"type": "json_api", "why": "a JSON API"}]})
    r, fake, db = _post(_script('{"kind": "pull", "shape": ', good), "values over the area from a public API")
    assert r.status_code == 200, r.text
    assert (r.json()["kind"], r.json()["shape"]) == ("pull", "field")
    assert fake.calls[1]["model"] == Settings(_env_file=None).agent_model("repair").model_id
    assert any(t.why == "validated after repair" for t in db.list_trace("some-app"))


def test_options_that_do_not_fit_the_kind_are_invalid_and_repaired():
    wrong = json.dumps({"kind": "push", "shape": "point", "options": [{"type": "rss", "why": "x"}]})
    good = json.dumps({"kind": "push", "shape": "point", "options": [{"type": "post_api", "why": "x"}]})
    r, fake, _ = _post(_script(wrong, good), PUSH_DESC)
    assert len(fake.calls) == 2
    assert r.json()["options"][0]["type"] == "post_api"


def test_unknown_preset_is_invalid():
    bad = json.dumps(
        {"kind": "pull", "shape": "field", "options": [{"type": "json_api", "why": "x", "config": {"preset": "nope"}}]}
    )
    r, fake, _ = _post(_script(bad, bad), "values from an API")
    assert len(fake.calls) == 2
    assert _is_unranked_fallback(r.json())


def test_repair_also_invalid_returns_the_full_unranked_list():
    r, _, db = _post(_script("not json", "{still not json"), PUSH_DESC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert _is_unranked_fallback(body)
    assert {o["type"] for o in body["options"]} == {"post_api", "webhook", "rss", "json_api", "json_list", "local_file"}
    assert body["meaning"] == PUSH_DESC
    actions = [t.action for t in db.list_trace("some-app")]
    assert "gave up" in actions and "fallback" in actions


def test_empty_rate_budget_returns_the_unranked_list_and_traces_the_skip():
    r, fake, db = _post(_script("unused"), PUSH_DESC, empty_budget=True)
    assert r.status_code == 200, r.text
    assert _is_unranked_fallback(r.json())
    assert fake.calls == []
    assert any(t.skipped_reason == "rate budget" for t in db.list_trace("some-app"))


def test_provider_error_returns_the_unranked_list():
    script = {"scenario": "s", "description": "d", "steps": [{"error": {"status": 400, "message": "bad"}}]}
    r, _, _ = _post(script, PUSH_DESC)
    assert r.status_code == 200 and _is_unranked_fallback(r.json())


def test_meaning_is_the_description_verbatim_even_with_surrounding_space():
    desc = "  readings sent by our app  "
    r, _, _ = _post("source_suggest_push", desc)
    assert r.json()["meaning"] == desc


def test_empty_description_is_rejected():
    r, fake, _ = _post(_script("unused"), "")
    assert r.status_code == 422
    assert fake.calls == []


def test_catalogue_has_no_html_scraper_and_every_preset_from_the_connector_catalogue():
    from app.connectors.presets import PRESETS

    opts = catalogue()
    assert {o.type for o in opts} == {"post_api", "webhook", "rss", "json_api", "json_list", "local_file"}
    assert {o.config["preset"] for o in opts if o.config and "preset" in o.config} == set(PRESETS)


# -- harmless slips seen in live runs (#164 follow-on): fixed in code, no repair call ----------


def test_prose_style_type_and_junk_presets_are_cleaned_without_a_repair():
    slipped = json.dumps(
        {
            "kind": "pull",
            "shape": "field",
            "options": [
                {"type": "json_api preset=open_meteo", "why": "a weather preset"},
                {"type": "rss", "why": "a feed", "config": {"preset": "rss", "format": "xml"}},
                {"type": "post_api", "why": "wrong kind, dropped"},
            ],
        }
    )
    r, fake, db = _post(_script(slipped), "current conditions over the area from a public service")
    assert r.status_code == 200, r.text
    assert len(fake.calls) == 1  # no repair call
    types = _types(r.json())
    assert types[0] == ("json_api", "open_meteo") and types[1] == ("rss", None)
    assert all(o["type"] in ("rss", "json_api", "json_list", "local_file") for o in r.json()["options"])
    assert any(t.why == "validated" for t in db.list_trace("some-app"))


def test_push_options_lose_any_config():
    slipped = json.dumps(
        {
            "kind": "push",
            "shape": "point",
            "options": [{"type": "post_api", "why": "x", "config": {"preset": "post_api"}}],
        }
    )
    r, fake, _ = _post(_script(slipped), PUSH_DESC)
    assert len(fake.calls) == 1 and r.json()["options"][0] == {**r.json()["options"][0], "config": None}


def test_a_url_the_dev_gave_is_never_replaced_by_a_preset():
    desc = "hourly readings published as JSON at https://data.example.org/readings.json."
    picked_preset = json.dumps(
        {
            "kind": "pull",
            "shape": "field",
            "options": [{"type": "json_api", "why": "x", "config": {"preset": "open_meteo"}}],
        }
    )
    r, _, _ = _post(_script(picked_preset), desc)
    first = r.json()["options"][0]
    assert first["type"] == "json_api" and first["config"] == {"url": "https://data.example.org/readings.json"}
    # a feed the model chose without the URL gets the dev's URL
    feed = "a public feed at https://feeds.example.org/area.xml"
    no_url = json.dumps({"kind": "pull", "shape": "document", "options": [{"type": "rss", "why": "x"}]})
    r2, _, _ = _post(_script(no_url), feed)
    assert r2.json()["options"][0]["config"] == {"url": "https://feeds.example.org/area.xml"}


def test_task_prompt_shows_each_option_as_the_json_to_return():
    from app.agents.source_suggester import task_prompt

    text = task_prompt(INTENT, PUSH_DESC)
    assert '{"type": "json_api", "config": {"preset": "open_meteo"}}' in text
    assert "type=" not in text
