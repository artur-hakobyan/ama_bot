"""Only real network drops are silent. A rejected message is a bug to surface.

python-telegram-bot makes BadRequest a subclass of NetworkError, so "Can't
parse entities" (a broken Markdown preview) was logged as a "network hiccup"
and the operator was left with no reply and no error at all.
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import Update
from telegram.error import BadRequest, NetworkError

from bot.main import error_handler


def _update():
    u = Update(update_id=1)
    msg = SimpleNamespace(reply_text=AsyncMock())
    object.__setattr__(u, "_effective_message", msg)
    return u, msg


async def _run(err, monkeypatch):
    u, msg = _update()
    monkeypatch.setattr(Update, "effective_message", property(lambda self: msg))
    await error_handler(u, SimpleNamespace(error=err))
    return msg


async def test_a_dropped_connection_stays_quiet(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        msg = await _run(NetworkError("Bad Gateway"), monkeypatch)
    msg.reply_text.assert_not_awaited()
    assert "Network hiccup" in caplog.text


async def test_a_double_tap_is_ignored(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        msg = await _run(BadRequest("Message is not modified: specified new "
                                    "message content ..."), monkeypatch)
    msg.reply_text.assert_not_awaited()
    assert "hiccup" not in caplog.text


async def test_a_rejected_message_is_reported(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        msg = await _run(BadRequest("Can't parse entities: can't find end of "
                                    "the entity"), monkeypatch)
    msg.reply_text.assert_awaited_once()
    assert "Can't parse entities" in caplog.text
