from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.claude_client import (ClaudeClient, ClaudeError,
                               EmptyResponseError)


def _text_response(text: str):
    """A response object shaped like the API's, for use in a side_effect list."""
    return SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text),
    ])


def fake_anthropic(text=None, side_effect=None):
    # Real responses may lead with a thinking block, so blocks carry a type.
    resp = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text or ""),
    ])
    create = AsyncMock(return_value=resp, side_effect=side_effect)
    return SimpleNamespace(messages=SimpleNamespace(create=create)), create


async def test_draft_article_parses_json():
    fake, _ = fake_anthropic('```json\n{"title_a":"A","title_b":"B","body_html":"<p>x</p>","summary":"s","tags":["wand"]}\n```')
    c = ClaudeClient("k", "m", client=fake)
    d = await c.draft_article("Dachschräge", "Silent Jelly", "-")
    assert d["title_a"] == "A" and d["tags"] == ["wand"]


async def test_draft_article_bad_json_raises():
    fake, _ = fake_anthropic("not json at all")
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError):
        await c.draft_article("t", "d", "-")


async def test_retries_then_raises(monkeypatch):
    monkeypatch.setattr(ClaudeClient, "RETRY_BASE_DELAY", 0.0)
    fake, create = fake_anthropic(side_effect=RuntimeError("boom"))
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError, match="boom"):
        await c.alt_text("bild")
    assert create.await_count == ClaudeClient.MAX_ATTEMPTS


async def test_a_transient_failure_is_survived(monkeypatch):
    """One 500 killed a whole article mid-batch; the next attempt succeeds."""
    monkeypatch.setattr(ClaudeClient, "RETRY_BASE_DELAY", 0.0)
    fake, create = fake_anthropic(side_effect=[
        RuntimeError("Internal server error"),
        _text_response("ein Bild"),
    ])
    c = ClaudeClient("k", "m", client=fake)
    assert await c.alt_text("bild") == "ein Bild"
    assert create.await_count == 2


async def test_the_error_says_it_is_temporary(monkeypatch):
    """The operator sees this message; it should say what to do about it."""
    monkeypatch.setattr(ClaudeClient, "RETRY_BASE_DELAY", 0.0)
    fake, _ = fake_anthropic(side_effect=RuntimeError("Internal server error"))
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError, match="temporary"):
        await c.alt_text("bild")


async def test_draft_article_missing_keys_raises():
    fake, _ = fake_anthropic('{"title_a": "A"}')
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError, match="issing keys"):
        await c.draft_article("t", "d", "-")


async def test_self_check():
    fake, _ = fake_anthropic('{"ok": false, "issues": ["zu werblich"]}')
    c = ClaudeClient("k", "m", client=fake)
    r = await c.self_check({"title_a": "A", "body_html": "<p></p>", "summary": ""})
    assert r["ok"] is False and r["issues"] == ["zu werblich"]


async def test_draft_article_uses_structured_outputs():
    fake, create = fake_anthropic('{"title_a":"A","title_b":"B","body_html":"<p>x</p>","summary":"s","tags":["t"]}')
    c = ClaudeClient("k", "m", client=fake)
    await c.draft_article("t", "d", "-")
    fmt = create.await_args.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "body_html" in fmt["schema"]["properties"]


async def test_effort_omitted_for_models_that_reject_it():
    """Haiku returns 400 for the effort parameter, so it must never be sent."""
    fake, create = fake_anthropic('{"ok": true, "issues": []}')
    haiku = ClaudeClient("k", "claude-haiku-4-5", client=fake)
    await haiku._ask("x", output_schema={"type": "object"}, effort="low")
    cfg = create.await_args.kwargs.get("output_config", {})
    assert "effort" not in cfg
    assert "format" in cfg          # schema still applied

    fake2, create2 = fake_anthropic('{"ok": true, "issues": []}')
    sonnet = ClaudeClient("k", "claude-sonnet-5", client=fake2)
    await sonnet._ask("x", output_schema={"type": "object"}, effort="low")
    assert create2.await_args.kwargs["output_config"]["effort"] == "low"


# --- empty responses --------------------------------------------------------

def _no_text_response():
    """What the API returns when the budget went entirely on thinking."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="…")],
        stop_reason="max_tokens")


async def test_an_empty_response_is_not_retried(monkeypatch):
    """Three two-minute attempts at a request that cannot improve is a 6-minute
    wait ending in the same failure — the operator saw exactly that."""
    monkeypatch.setattr(ClaudeClient, "RETRY_BASE_DELAY", 0.0)
    fake, create = fake_anthropic()
    create.return_value = _no_text_response()
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(EmptyResponseError):
        await c.alt_text("bild")
    assert create.await_count == 1, "an unimprovable request was retried"


async def test_the_empty_response_error_names_the_cause(monkeypatch):
    """"no text block" sent the reader hunting for a network fault."""
    monkeypatch.setattr(ClaudeClient, "RETRY_BASE_DELAY", 0.0)
    fake, create = fake_anthropic()
    create.return_value = _no_text_response()
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(EmptyResponseError, match="without writing an answer"):
        await c.alt_text("bild")


async def test_an_empty_response_is_still_a_claude_error(monkeypatch):
    """Call sites catch ClaudeError; the new type must not escape them."""
    monkeypatch.setattr(ClaudeClient, "RETRY_BASE_DELAY", 0.0)
    fake, create = fake_anthropic()
    create.return_value = _no_text_response()
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError):
        await c.alt_text("bild")


async def test_apply_review_notes_caps_the_thinking_budget():
    """The one large call that lacked an effort cap, and so returned no text."""
    captured = {}

    class Recorder(ClaudeClient):
        async def _ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return "<p>ok</p>"

    c = Recorder("k", "claude-sonnet-5", client=SimpleNamespace(
        messages=SimpleNamespace(create=None)))
    await c.apply_review_notes("<p>x</p>", [{"quote": "q", "note": "n",
                                             "kind": "comment"}])
    assert captured.get("effort") == "low", "no effort cap on a 16k-token call"
