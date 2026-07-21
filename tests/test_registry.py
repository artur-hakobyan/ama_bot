from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.modules.registry import Services, main_menu_keyboard

def test_services_holds_dependencies():
    s = Services(config=1, db=2, shopify=3, claude=4)
    assert (s.config, s.db, s.shopify, s.claude) == (1, 2, 3, 4)

def test_main_menu_keyboard():
    mod = SimpleNamespace(NAME="blog", MENU_LABEL="📝 Blog")
    kb = main_menu_keyboard([mod])
    rows = kb.inline_keyboard
    assert rows[0][0].text == "📝 Blog" and rows[0][0].callback_data == "blog:menu"
    assert rows[-1][0].callback_data == "noop"
    assert "coming soon" in rows[-1][0].text.lower()

import pytest
from bot.config import Config
from bot.db import Database
from bot.main import error_handler, main_menu_cb, start_cmd, text_router

CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t", "BOT_PASSWORD": "geheim",
    "ALLOWLIST_USER_IDS": "111", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "g",
})

@pytest.fixture
def services(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    yield Services(config=CFG, db=db, shopify=None, claude=None)
    db.close()

def make_update(user_id, text=""):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           effective_message=msg, message=msg)

def make_context(services, modules=()):
    return SimpleNamespace(bot_data={"services": services,
                                     "modules": list(modules)})

async def test_start_silent_for_stranger(services):
    u = make_update(999)
    await start_cmd(u, make_context(services))
    u.effective_message.reply_text.assert_not_awaited()

async def test_password_flow(services):
    ctx = make_context(services)
    u = make_update(111, "falsch")
    await text_router(u, ctx)
    assert "Falsches" in u.effective_message.reply_text.await_args.args[0]
    u2 = make_update(111, "geheim")
    await text_router(u2, ctx)
    assert services.db.get_session(111)["unlocked"] is True

async def test_router_dispatches_to_module_step(services):
    handle = AsyncMock()
    mod = SimpleNamespace(NAME="blog", MENU_LABEL="B", handle_step=handle)
    services.db.set_unlocked(111, True)
    services.db.set_step(111, "blog:topic")
    u = make_update(111, "Dachschräge")
    await text_router(u, make_context(services, [mod]))
    handle.assert_awaited_once()
    assert handle.await_args.args[0] == "blog:topic"


def make_cb_update(user_id):
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           callback_query=query)


async def test_main_menu_cb_clears_lingering_step(services):
    services.db.set_step(111, "blog:topic", {})
    u = make_cb_update(111)
    await main_menu_cb(u, make_context(services))
    assert services.db.get_session(111)["step"] is None


async def test_error_handler_does_not_raise_for_non_update():
    ctx = SimpleNamespace(error=RuntimeError("boom"))
    await error_handler(None, ctx)
