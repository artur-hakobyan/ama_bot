from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.auth import authorized, is_allowlisted
from bot.config import Config

CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t",
    "ALLOWLIST_USER_IDS": "111,222,333", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "g",
})


def test_allowlist():
    assert is_allowlisted(CFG, 111) and is_allowlisted(CFG, 333)
    assert not is_allowlisted(CFG, 999)


def make_update(user_id):
    msg = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           effective_message=msg)


def make_context(db):
    services = SimpleNamespace(config=CFG, db=db)
    return SimpleNamespace(bot_data={"services": services})


@pytest.fixture
def db(tmp_path):
    from bot.db import Database
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


async def test_denied_is_silent(db):
    inner = AsyncMock()
    update = make_update(999)
    await authorized(inner)(update, make_context(db))
    inner.assert_not_awaited()
    update.effective_message.reply_text.assert_not_awaited()
    row = db._conn.execute("SELECT * FROM audit_log").fetchone()
    assert row["result"] == "denied"


async def test_allowlisted_runs_handler(db):
    inner = AsyncMock()
    update = make_update(111)
    await authorized(inner)(update, make_context(db))
    inner.assert_awaited_once()
