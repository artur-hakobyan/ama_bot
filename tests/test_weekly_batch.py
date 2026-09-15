"""The weekly batch must survive an operator it cannot reach.

Telegram forbids a bot writing first, so send_message fails with "Chat not
found" until the operator presses Start. On 14 Sep that raised an unhandled
exception and buried a run that had already succeeded for someone else.
"""
import logging

import pytest
from telegram.error import BadRequest, Forbidden

from bot.db import Database
from bot.main import weekly_batch_job


class FakeConfig:
    def __init__(self, ids):
        self.allowlist_user_ids = frozenset(ids)


class FakeServices:
    def __init__(self, db, ids):
        self.db = db
        self.config = FakeConfig(ids)


class FakeContext:
    def __init__(self, services):
        self.bot_data = {"services": services}


@pytest.fixture
def ctx(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    yield FakeContext(FakeServices(db, [1, 2, 3]))
    db.close()


async def test_one_unreachable_operator_does_not_stop_the_others(ctx, monkeypatch):
    reached = []

    async def fake_batch(context, user_id):
        if user_id == 2:
            raise BadRequest("Chat not found")
        reached.append(user_id)

    monkeypatch.setattr("bot.modules.blog.scheduled_batch", fake_batch)
    await weekly_batch_job(ctx)
    assert reached == [1, 3], "the run stopped at the unreachable operator"


async def test_chat_not_found_is_recorded_as_a_setup_step(ctx, monkeypatch):
    """It is the operator's missing /start, not a fault in the run."""
    async def fake_batch(context, user_id):
        raise BadRequest("Chat not found")

    monkeypatch.setattr("bot.modules.blog.scheduled_batch", fake_batch)
    await weekly_batch_job(ctx)
    rows = [r for r in ctx.bot_data["services"].db.recent_audit(50)
            if r["action"] == "weekly_batch"]
    assert rows and all(r["result"] == "skipped" for r in rows)
    assert any("Start" in (r["detail"] or "") for r in rows)


async def test_a_blocked_bot_is_skipped_too(ctx, monkeypatch):
    async def fake_batch(context, user_id):
        raise Forbidden("bot was blocked by the user")

    monkeypatch.setattr("bot.modules.blog.scheduled_batch", fake_batch)
    await weekly_batch_job(ctx)          # must not raise
    rows = [r for r in ctx.bot_data["services"].db.recent_audit(50)
            if r["action"] == "weekly_batch"]
    assert any("blocked" in (r["detail"] or "") for r in rows)


async def test_an_unrelated_badrequest_is_still_a_real_error(ctx, monkeypatch,
                                                            caplog):
    """Only "chat not found" is a setup step; other BadRequests are bugs."""
    async def fake_batch(context, user_id):
        raise BadRequest("message text is empty")

    monkeypatch.setattr("bot.modules.blog.scheduled_batch", fake_batch)
    with caplog.at_level(logging.ERROR):
        await weekly_batch_job(ctx)
    assert "Weekly batch failed" in caplog.text


async def test_reaching_nobody_is_reported_loudly(ctx, monkeypatch, caplog):
    """Silence is the dangerous outcome: the whole point is the weekly nudge."""
    async def fake_batch(context, user_id):
        raise BadRequest("Chat not found")

    monkeypatch.setattr("bot.modules.blog.scheduled_batch", fake_batch)
    with caplog.at_level(logging.ERROR):
        await weekly_batch_job(ctx)
    assert "reached nobody" in caplog.text


def test_has_started_tracks_who_opened_the_bot(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert db.has_started(42) is False
    db.get_session(42)                   # what /start does
    assert db.has_started(42) is True
    db.close()
