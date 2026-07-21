from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.config import Config
from bot.db import Database
from bot.modules import blog
from bot.modules.registry import Services


def make_draft(**over):
    d = {"id": "abc123", "user_id": 1, "shopify_article_gid": "gid://shopify/Article/9",
         "title_a": "Titel A", "title_b": "Titel B", "chosen_title": "a",
         "body_html": "<p>x</p>", "summary": "Zusammenfassung", "tags": ["wand"],
         "status": "pending"}
    d.update(over)
    return d


def test_parse_cb():
    assert blog.parse_cb("blog:pub:abc123") == ("pub", "abc123")
    assert blog.parse_cb("blog:new") == ("new", None)


def test_chosen_title():
    assert blog.chosen_title(make_draft()) == "Titel A"
    assert blog.chosen_title(make_draft(chosen_title="b")) == "Titel B"


def test_preview_text_contains_essentials():
    text = blog.preview_text(make_draft(), "https://x/admin/articles/9", ["zu lang"])
    assert "Titel A" in text and "Zusammenfassung" in text
    assert "https://x/admin/articles/9" in text and "zu lang" in text


def test_md_escape_escapes_specials():
    assert blog.md_escape("Wand *mit* _Charme_") == r"Wand \*mit\* \_Charme\_"
    assert blog.md_escape("a`b[c") == r"a\`b\[c"


def test_preview_text_escapes_dynamic_markdown():
    draft = make_draft(title_a="Wand *mit* _Charme_")
    text = blog.preview_text(draft, "https://x/admin/articles/9", [])
    assert "*mit*" not in text
    assert r"\*mit\*" in text
    assert r"\_Charme\_" in text


def test_preview_keyboard_actions():
    kb = blog.preview_keyboard("abc123")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == ["blog:pub:abc123", "blog:regen:abc123", "blog:title:abc123",
                     "blog:editdraft:abc123", "blog:discard:abc123"]


def test_design_slug_roundtrip():
    assert blog.DESIGNS["jelly"] == "Silent Jelly"
    assert blog.DESIGNS["unberuehrt"] == "Unberührt"
    assert blog.DESIGNS["poppy"] == "Poppy Seed Explosion"
    assert blog.DESIGNS["none"] == "No specific design"


CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t", "BOT_PASSWORD": "pw",
    "ALLOWLIST_USER_IDS": "111", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "gid://shopify/Blog/1",
})


@pytest.fixture
def services(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.set_unlocked(111, True)
    shopify = SimpleNamespace(
        create_article=AsyncMock(return_value={"id": "gid://shopify/Article/9",
                                               "title": "Titel A", "handle": "titel-a",
                                               "isPublished": False}),
        publish_article=AsyncMock(return_value={"id": "gid://shopify/Article/9",
                                                "title": "Titel A", "handle": "titel-a",
                                                "isPublished": True}),
        delete_article=AsyncMock(return_value="gid://shopify/Article/9"),
        update_article=AsyncMock(),
        list_articles=AsyncMock(return_value=("news", [])),
        admin_url=lambda gid: f"https://d/admin/articles/{gid.rsplit('/', 1)[-1]}",
        live_url=lambda bh, ah: f"https://d/blogs/{bh}/{ah}",
    )
    claude = SimpleNamespace(
        draft_article=AsyncMock(return_value={
            "title_a": "Titel A", "title_b": "Titel B", "body_html": "<p>x</p>",
            "summary": "Zsf", "tags": ["wand"]}),
        self_check=AsyncMock(return_value={"ok": True, "issues": []}),
        revise_article=AsyncMock(return_value="<p>neu</p>"),
    )
    shopify.get_article = AsyncMock(return_value={
        "id": "gid://shopify/Article/5", "title": "Alt", "handle": "alt",
        "body": "<p>alt</p>", "isPublished": True})
    yield Services(config=CFG, db=db, shopify=shopify, claude=claude)
    db.close()


def make_ctx(services):
    return SimpleNamespace(bot_data={"services": services, "modules": []})


def make_text_update(text):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=111),
                           effective_message=msg, message=msg)


async def test_full_new_article_flow(services):
    ctx = make_ctx(services)
    # Q1: topic
    services.db.set_step(111, "blog:topic", {})
    await blog.handle_step("blog:topic", make_text_update("Dachschräge"), ctx)
    s = services.db.get_session(111)
    assert s["step"] is None  # waiting on design button, not a text step
    assert s["context"]["topic"] == "Dachschräge"
    # Q2 (design) is a callback; simulate its effect then Q3: must-include text
    services.db.set_step(111, "blog:must", {"topic": "Dachschräge", "design": "Silent Jelly"})
    u = make_text_update("-")
    await blog.handle_step("blog:must", u, ctx)
    # draft created in shopify as draft + db row exists
    services.shopify.create_article.assert_awaited_once()
    assert services.shopify.create_article.await_args.args[0] == CFG.blog_id
    # preview replied with keyboard
    reply = u.effective_message.reply_text
    assert reply.await_count >= 1
    kwargs = reply.await_args.kwargs
    assert kwargs.get("reply_markup") is not None


def make_cb_update(data):
    query = SimpleNamespace(data=data, answer=AsyncMock(),
                            edit_message_text=AsyncMock())
    msg = SimpleNamespace(text="", reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=111),
                           callback_query=query, effective_message=msg)


async def test_list_for_delete_shows_articles(services):
    services.shopify.list_articles = AsyncMock(return_value=("news", [
        {"id": "gid://shopify/Article/5", "title": "Alt", "handle": "alt",
         "isPublished": True, "publishedAt": "2026-01-01"}]))
    u = make_cb_update("blog:listdel")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    kb = u.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "blog:pickdel:5"

async def test_delete_requires_confirm_then_deletes(services):
    u = make_cb_update("blog:pickdel:5")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    kb = u.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert any(b.callback_data == "blog:confdel:5"
               for row in kb.inline_keyboard for b in row)
    services.shopify.delete_article.assert_not_awaited()
    u2 = make_cb_update("blog:confdel:5")
    await blog.callbacks.__wrapped__(u2, make_ctx(services))
    services.shopify.delete_article.assert_awaited_once_with("gid://shopify/Article/5")

async def test_edit_existing_title_flow(services):
    u = make_cb_update("blog:exttitle:5")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    assert services.db.get_session(111)["step"] == "blog:exttitle"
    ut = make_text_update("Neuer Titel")
    await blog.handle_step("blog:exttitle", ut, make_ctx(services))
    services.shopify.update_article.assert_awaited_with(
        "gid://shopify/Article/5", {"title": "Neuer Titel"})


async def test_menu_callback_clears_lingering_step(services):
    services.db.set_step(111, "blog:topic", {})
    u = make_cb_update("blog:menu")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    assert services.db.get_session(111)["step"] is None


async def test_create_and_preview_without_topic_shows_warning(services):
    ctx = make_ctx(services)
    services.db.set_step(111, "blog:must", {"design": "X"})
    u = make_text_update("-")
    await blog.handle_step("blog:must", u, ctx)
    services.claude.draft_article.assert_not_awaited()
    reply = u.effective_message.reply_text
    assert "Original inputs" in reply.await_args.args[0]


async def test_design_none_stores_german_value_for_claude(services):
    services.db.set_step(111, None, {"topic": "Dachschräge"})
    u = make_cb_update("blog:design:none")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    s = services.db.get_session(111)
    assert s["context"]["design"] == "kein bestimmtes Design"
    assert s["step"] == "blog:must"


def test_html_to_telegram_structure():
    html = ('<h2>Warum große Bilder?</h2><p>Ein <strong>ruhiger</strong> Raum '
            '&amp; mehr.</p><ul><li>Punkt eins</li><li>Punkt zwei</li></ul>')
    out = blog.html_to_telegram(html)
    assert "<b>Warum große Bilder?</b>" in out
    assert "<b>ruhiger</b>" in out
    assert "&amp; mehr." in out
    assert "• Punkt eins" in out and "• Punkt zwei" in out
    assert "<p>" not in out and "<ul>" not in out


def test_chunk_text_splits_on_paragraphs():
    text = "\n\n".join(f"Absatz {i} " + "x" * 100 for i in range(80))
    chunks = blog.chunk_text(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks).count("Absatz") == 80


async def test_demo_mode_skips_shopify_and_sends_article(services):
    import dataclasses
    services.config = dataclasses.replace(CFG, shopify_enabled=False)
    ctx = make_ctx(services)
    services.db.set_step(111, "blog:must", {"topic": "Dachschräge", "design": "Silent Jelly"})
    u = make_text_update("-")
    await blog.handle_step("blog:must", u, ctx)
    services.shopify.create_article.assert_not_awaited()
    texts = [c.args[0] for c in u.effective_message.reply_text.await_args_list]
    assert any("Shopify not connected" in t for t in texts)  # preview status line
    assert any("<b>" in t or "x" in t or len(t) > 100 for t in texts)  # article body sent


async def test_demo_mode_publish_says_coming_soon(services):
    import dataclasses
    services.config = dataclasses.replace(CFG, shopify_enabled=False)
    did = services.db.create_draft(111, "TA", "TB", "<p>x</p>", "s", [])
    u = make_cb_update(f"blog:pub:{did}")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    services.shopify.publish_article.assert_not_awaited()
    reply = u.effective_message.reply_text.await_args.args[0]
    assert "ready soon" in reply
    assert services.db.get_draft(did) is not None  # draft kept


async def test_demo_mode_list_existing_says_coming_soon(services):
    import dataclasses
    services.config = dataclasses.replace(CFG, shopify_enabled=False)
    u = make_cb_update("blog:listedit")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    services.shopify.list_articles.assert_not_awaited()
    assert "ready soon" in u.callback_query.edit_message_text.await_args.args[0]
