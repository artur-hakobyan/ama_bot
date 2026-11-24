"""Publishing must finish cleanly and move a batch on to its next article.

On 2026-11-09 a refactor left `ctx` undefined in the publish handler: the
article went live, then the handler crashed with NameError, the operator saw
"Unexpected error", tapped Publish again (three times), and the batch never
advanced — so it stayed "running" and the weekly batch skipped that operator.
"""
from unittest.mock import AsyncMock

import pytest

from bot.modules import blog
from tests.test_blog import make_cb_update as _cb
from tests.test_blog import make_ctx, services  # noqa: F401


def make_cb_update(data):
    u = _cb(data)
    u.effective_user.first_name = "Ann"
    u.effective_user.username = None
    u.callback_query.message = u.effective_message
    return u


@pytest.fixture
def quiet(monkeypatch):
    """Silence the side trips after publishing; record the batch hand-off."""
    started = []

    async def nothing(*a, **k):
        return None

    async def next_in_batch(update, context, batch_id, user_id):
        started.append(batch_id)

    monkeypatch.setattr(blog, "_offer_images", nothing)
    monkeypatch.setattr(blog, "notify_team", nothing)
    monkeypatch.setattr(blog, "_run_next_in_batch", next_in_batch)
    return started


def _batch_draft(services):
    proposals = [{"keyword": "eins", "title": "T1"}, {"keyword": "zwei", "title": "T2"}]
    bid = services.db.create_batch(111, "Büro", proposals)
    services.db.update_batch(bid, status="running")
    did = services.db.create_draft(111, "TA", "TB", "<p>x</p>", "s", [])
    ctx = services.db.get_session(111)["context"]
    ctx["batch_id"] = bid
    services.db.set_step(111, None, ctx)
    return bid, did


async def test_publishing_a_batch_article_starts_the_next_one(services, quiet):
    bid, did = _batch_draft(services)
    u = make_cb_update(f"blog:pub:{did}")
    await blog.callbacks.__wrapped__(u, make_ctx(services))   # must not raise
    assert services.db.get_draft(did)["status"] == "published"
    assert services.db.get_batch(bid)["current_index"] == 1
    assert quiet == [bid]


async def test_a_second_tap_does_not_publish_twice(services, quiet):
    bid, did = _batch_draft(services)
    ctx = make_ctx(services)
    await blog.callbacks.__wrapped__(make_cb_update(f"blog:pub:{did}"), ctx)
    services.shopify.create_article = AsyncMock()
    second = make_cb_update(f"blog:pub:{did}")
    await blog.callbacks.__wrapped__(second, ctx)
    assert services.shopify.publish_article.await_count == 1
    assert services.db.get_batch(bid)["current_index"] == 1   # not skipped ahead
    assert "already" in second.callback_query.edit_message_text.await_args.args[0].lower()
