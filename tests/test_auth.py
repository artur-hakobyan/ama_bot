from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.auth import authorized, check_password, is_allowlisted
from bot.config import Config

CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t", "BOT_PASSWORD": "geheim",
    "ALLOWLIST_USER_IDS": "111", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "g",
})

def test_allowlist_and_password():
    assert is_allowlisted(CFG, 111) and not is_allowlisted(CFG, 999)
    assert check_password(CFG, "geheim") and not check_password(CFG, "nope")

def make_update(user_id):
    msg = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           effective_message=msg)

def make_context(db, unlocked):
    services = SimpleNamespace(config=CFG, db=db)
    db.set_unlocked(111, unlocked)
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
    await authorized(inner)(update, make_context(db, True))
    inner.assert_not_awaited()
    update.effective_message.reply_text.assert_not_awaited()

async def test_locked_prompts_password(db):
    inner = AsyncMock()
    update = make_update(111)
    await authorized(inner)(update, make_context(db, False))
    inner.assert_not_awaited()
    update.effective_message.reply_text.assert_awaited_once()

async def test_unlocked_runs_handler(db):
    inner = AsyncMock()
    update = make_update(111)
    await authorized(inner)(update, make_context(db, True))
    inner.assert_awaited_once()
