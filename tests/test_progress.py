"""The waiting animation: it must spin, follow stage labels, and never raise."""
import asyncio

import pytest

from bot.modules.blog import (Progress, SPINNER_EMOJI_ID, SPINNER_FRAMES,
                              eta_line)


class FakeMessage:
    def __init__(self, fail=False):
        self.texts = []
        self._fail = fail

    async def edit_text(self, text, **kwargs):
        if self._fail:
            raise RuntimeError("Flood control exceeded")
        self.texts.append(text)


@pytest.mark.asyncio
async def test_spinner_animates_while_the_step_runs(monkeypatch):
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    async with Progress(msg, "✍️ Writing"):
        await asyncio.sleep(0.06)
    assert len(msg.texts) >= 3, "spinner never advanced"
    frames = {t.strip()[-1] for t in msg.texts}
    assert len(frames) >= 3, "the same frame was repeated"
    assert all(f in SPINNER_FRAMES for f in frames)
    assert all("✍️ Writing" in t for t in msg.texts)
    # The animated emoji must be present, with a plain glyph inside it so the
    # line still reads for anyone whose client drops the entity.
    assert all(SPINNER_EMOJI_ID in t for t in msg.texts)
    assert all("⏳</tg-emoji>" in t for t in msg.texts)


@pytest.mark.asyncio
async def test_label_changes_without_stopping_the_spin(monkeypatch):
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    async with Progress(msg, "✍️ Writing") as p:
        await p.set_label("editor pass")
        await asyncio.sleep(0.04)
    assert any("editor pass" in t for t in msg.texts)
    assert "✍️ Writing — editor pass" in msg.texts[-1]


@pytest.mark.asyncio
async def test_done_replaces_the_spinner_with_static_text(monkeypatch):
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    p = Progress(msg, "✍️ Writing")
    async with p:
        await asyncio.sleep(0.02)
    await p.done("✅ Done.")
    assert msg.texts[-1] == "✅ Done."
    assert msg.texts[-1][-1] not in SPINNER_FRAMES


@pytest.mark.asyncio
async def test_telegram_rate_limit_never_reaches_the_caller(monkeypatch):
    """A cosmetic edit must not abort an article that took two minutes to write."""
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage(fail=True)
    async with Progress(msg, "✍️ Writing") as p:
        await p.set_label("still fine")
        await asyncio.sleep(0.03)
    await p.done("✅ Done.")


@pytest.mark.asyncio
async def test_the_task_is_stopped_on_exit(monkeypatch):
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    p = Progress(msg, "✍️ Writing")
    async with p:
        await asyncio.sleep(0.02)
    count = len(msg.texts)
    await asyncio.sleep(0.05)
    assert len(msg.texts) == count, "spinner kept editing after the block ended"


@pytest.mark.asyncio
async def test_an_exception_inside_the_block_stops_the_spinner(monkeypatch):
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    p = Progress(msg, "✍️ Writing")
    with pytest.raises(ValueError):
        async with p:
            await asyncio.sleep(0.02)
            raise ValueError("Claude failed")
    count = len(msg.texts)
    await asyncio.sleep(0.05)
    assert len(msg.texts) == count


@pytest.mark.asyncio
async def test_the_eta_is_shown_while_waiting(monkeypatch):
    """A long wait needs a stated duration, or it reads as a hang."""
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    async with Progress(msg, "✍️ Writing", eta_seconds=210):
        await asyncio.sleep(0.02)
    assert all("about 4 minutes" in t for t in msg.texts)


@pytest.mark.asyncio
async def test_no_eta_line_when_none_is_given(monkeypatch):
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    async with Progress(msg, "✍️ Writing"):
        await asyncio.sleep(0.02)
    assert all("Takes" not in t for t in msg.texts)


def test_eta_line_reads_naturally():
    assert eta_line(45) == "about 1 minute"
    assert eta_line(210) == "about 4 minutes"
    assert eta_line(120) == "about 2 minutes"


@pytest.mark.asyncio
async def test_a_label_with_html_characters_cannot_break_the_message(monkeypatch):
    """Labels reach parse_mode=HTML, so an unescaped < would drop the message."""
    monkeypatch.setattr("bot.modules.blog.SPINNER_INTERVAL", 0.01)
    msg = FakeMessage()
    async with Progress(msg, "✍️ Writing") as p:
        await p.set_label("<b>keyword</b> & links")
    assert any("&lt;b&gt;keyword&lt;/b&gt; &amp; links" in t for t in msg.texts)
