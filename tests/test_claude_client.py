from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.claude_client import ClaudeClient, ClaudeError


def fake_anthropic(text=None, side_effect=None):
    resp = SimpleNamespace(content=[SimpleNamespace(text=text or "")])
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


async def test_retry_once_then_raise():
    fake, create = fake_anthropic(side_effect=RuntimeError("boom"))
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError, match="boom"):
        await c.alt_text("bild")
    assert create.await_count == 2


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
