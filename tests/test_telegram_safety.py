"""Messages must reach the operator even when their content is awkward.

2026-11-26: a Google Doc id containing "_" (…8uu_b1M9…) was pasted raw into a
Markdown preview; Telegram rejected it ("can't find end of the entity"), the
operator got "Unexpected error" instead of the article, and the batch stalled.
A button tapped while that article was being written then failed with "Query
is too old" before doing anything.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from bot.db import Database
from bot.modules import blog
from tests.test_blog import make_cb_update, make_ctx, make_draft, services  # noqa: F401

DOC = "https://docs.google.com/document/d/1eQKwyMQnhtEmXJxhAl1aRrVrobb4NakH8uu_b1M9eK8/edit"


def _unescaped_markers(text: str) -> list:
    """Markdown specials that would open an entity, ignoring link targets."""
    import re
    outside = re.sub(r"\]\([^)]*\)", "]()", text)      # URLs inside links are literal
    return [m.start() for m in re.finditer(r"(?<!\\)_", outside)]


def test_a_doc_link_with_an_underscore_cannot_break_the_preview():
    text = blog.preview_text(make_draft(), DOC, [])
    assert f"]({DOC})" in text
    assert _unescaped_markers(text) == []


async def test_a_rejected_markdown_message_is_resent_as_plain_text():
    send = AsyncMock(side_effect=[BadRequest("Can't parse entities: can't find "
                                             "end of the entity"), None])
    await blog.send_markdown(send, r"*Titel* mit \_Unterstrich "
                             + blog.md_link("Doc", DOC), reply_markup="kb")
    first, second = send.await_args_list
    assert first.kwargs["parse_mode"] == "Markdown"
    assert "parse_mode" not in second.kwargs
    assert second.args[0] == f"Titel mit _Unterstrich Doc: {DOC}"   # URL intact
    assert second.kwargs["reply_markup"] == "kb"        # buttons survive


async def test_other_telegram_errors_are_not_hidden():
    send = AsyncMock(side_effect=BadRequest("Chat not found"))
    with pytest.raises(BadRequest):
        await blog.send_markdown(send, "x")


async def test_a_stale_button_still_does_its_job(services):
    u = make_cb_update("blog:menu")
    u.callback_query.answer = AsyncMock(side_effect=BadRequest(
        "Query is too old and response timeout expired or query id is invalid"))
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    u.callback_query.edit_message_text.assert_awaited()     # menu still shown


def test_a_new_batch_replaces_the_unapproved_one(tmp_path):
    """Starting twice left the first batch "proposed" forever, and an open batch
    makes the weekly run skip that operator."""
    db = Database(str(tmp_path / "t.db"))
    first = db.create_batch(1, "P", [])
    running = db.create_batch(1, "P", [])
    db.update_batch(running, status="running")
    other = db.create_batch(2, "P", [])
    newest = db.create_batch(1, "P", [])
    assert db.get_batch(first)["status"] == "superseded"
    assert db.get_batch(running)["status"] == "running"   # approved work is kept
    assert db.get_batch(other)["status"] == "proposed"    # a colleague's is theirs
    assert db.get_batch(newest)["status"] == "proposed"
    db.close()
