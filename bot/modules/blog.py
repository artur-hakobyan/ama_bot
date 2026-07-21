import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.auth import authorized
from bot.claude_client import ClaudeError
from bot.shopify_client import ShopifyError

logger = logging.getLogger(__name__)

NAME = "blog"
MENU_LABEL = "📝 Blog"

DESIGNS = {
    "jelly": "Silent Jelly",
    "unberuehrt": "Unberührt",
    "poppy": "Poppy Seed Explosion",
    "none": "No specific design",
}


# --- pure helpers -----------------------------------------------------------

def parse_cb(data: str):
    parts = data.split(":", 2)          # "blog:action[:arg]"
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    return action, arg


def chosen_title(draft: dict) -> str:
    return draft["title_a"] if draft["chosen_title"] == "a" else draft["title_b"]


def md_escape(text: str) -> str:
    """Escape legacy-Markdown specials so dynamic content can't break parse_mode='Markdown'."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def preview_text(draft: dict, admin_url: str, issues: list) -> str:
    lines = [
        f"📄 *{md_escape(chosen_title(draft))}*",
        "",
        md_escape(draft["summary"] or ""),
        "",
        f"Tags: {md_escape(', '.join(draft['tags']))}" if draft["tags"] else "",
        f"Admin: {admin_url}",
        "",
        "Status: draft (unpublished)",
    ]
    if issues:
        lines += ["", "⚠️ Self-check:"] + [f"• {md_escape(i)}" for i in issues]
    return "\n".join(line for line in lines if line is not None)


def preview_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data=f"blog:pub:{draft_id}")],
        [InlineKeyboardButton("🔄 Regenerate", callback_data=f"blog:regen:{draft_id}"),
         InlineKeyboardButton("🔀 Title A/B", callback_data=f"blog:title:{draft_id}")],
        [InlineKeyboardButton("✏️ Edit", callback_data=f"blog:editdraft:{draft_id}"),
         InlineKeyboardButton("🗑 Discard", callback_data=f"blog:discard:{draft_id}")],
    ])


def blog_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 New article", callback_data="blog:new")],
        [InlineKeyboardButton("✏️ Edit existing", callback_data="blog:listedit")],
        [InlineKeyboardButton("🗑 Delete existing", callback_data="blog:listdel")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main:menu")],
    ])


def design_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"blog:design:{slug}")]
         for slug, label in DESIGNS.items()])


def article_gid(num: str) -> str:
    return f"gid://shopify/Article/{num}"


def article_list_keyboard(articles, action_prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for a in articles:
        num = a["id"].rsplit("/", 1)[-1]
        mark = "🟢" if a.get("isPublished") else "📝"
        rows.append([InlineKeyboardButton(
            f"{mark} {a['title'][:40]}", callback_data=f"blog:{action_prefix}:{num}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="blog:menu")])
    return InlineKeyboardMarkup(rows)


# --- draft creation flow ----------------------------------------------------

async def _create_and_preview(update, context, answers: dict, user_id: int):
    """Claude draft → self-check → Shopify draft article → preview message."""
    services = context.bot_data["services"]
    msg = update.effective_message
    topic = answers.get("topic")
    if not topic:
        await msg.reply_text(
            "⚠️ Original inputs are missing — please start over.",
            reply_markup=blog_menu_keyboard())
        return
    design = answers.get("design", "kein bestimmtes Design")
    await msg.reply_text("✍️ Claude is writing the draft …")
    try:
        draft_data = await services.claude.draft_article(
            topic, design, answers.get("must", "-"))
        check = await services.claude.self_check(draft_data)
    except ClaudeError as e:
        services.db.log_audit(user_id, "draft", "-", "error", str(e))
        await msg.reply_text(f"❌ Claude error: {e}")
        return
    draft_id = services.db.create_draft(
        user_id, draft_data["title_a"], draft_data["title_b"],
        draft_data["body_html"], draft_data["summary"], draft_data["tags"])
    try:
        article = await services.shopify.create_article(
            services.config.blog_id, draft_data["title_a"], draft_data["body_html"],
            draft_data["summary"], draft_data["tags"], services.config.author_name)
    except ShopifyError as e:
        services.db.delete_draft(draft_id)
        services.db.log_audit(user_id, "article_create", "-", "error", str(e))
        await msg.reply_text(f"❌ Shopify error: {e}")
        return
    services.db.update_draft(draft_id, shopify_article_gid=article["id"])
    services.db.log_audit(user_id, "article_create", article["id"], "ok",
                          f"draft {draft_id}")
    draft = services.db.get_draft(draft_id)
    await msg.reply_text(
        preview_text(draft, services.shopify.admin_url(article["id"]), check["issues"]),
        reply_markup=preview_keyboard(draft_id), parse_mode="Markdown",
        disable_web_page_preview=True)


# --- text-step handling (router calls this) ---------------------------------

async def handle_step(step: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    user_id = update.effective_user.id
    session = services.db.get_session(user_id)
    text = (update.effective_message.text or "").strip()
    ctx = session["context"]

    if step == "blog:topic":
        ctx["topic"] = text
        services.db.set_step(user_id, None, ctx)
        await update.effective_message.reply_text(
            "2/3 — Which design should be featured?",
            reply_markup=design_keyboard())
    elif step == "blog:must":
        ctx["must"] = text
        services.db.set_step(user_id, None, ctx)
        await _create_and_preview(update, context, ctx, user_id)
    elif step == "blog:editdraft":
        draft_id = ctx.get("draft_id")
        draft = services.db.get_draft(draft_id) if draft_id else None
        services.db.set_step(user_id, None, ctx)
        if draft is None:
            await update.effective_message.reply_text(
                "⚠️ This draft no longer exists.",
                reply_markup=blog_menu_keyboard())
            return
        await update.effective_message.reply_text("✍️ Claude is revising …")
        try:
            new_body = await services.claude.revise_article(draft["body_html"], text)
            await services.shopify.update_article(
                draft["shopify_article_gid"], {"body": new_body})
        except (ClaudeError, ShopifyError) as e:
            services.db.log_audit(user_id, "article_edit",
                                  draft["shopify_article_gid"], "error", str(e))
            await update.effective_message.reply_text(f"❌ Error: {e}")
            return
        services.db.update_draft(draft_id, body_html=new_body)
        services.db.log_audit(user_id, "article_edit",
                              draft["shopify_article_gid"], "ok", text[:200])
        draft = services.db.get_draft(draft_id)
        await update.effective_message.reply_text(
            preview_text(draft, services.shopify.admin_url(draft["shopify_article_gid"]), []),
            reply_markup=preview_keyboard(draft_id), parse_mode="Markdown",
            disable_web_page_preview=True)
    elif step == "blog:exttitle":
        gid = ctx.get("article_gid")
        services.db.set_step(user_id, None, ctx)
        try:
            await services.shopify.update_article(gid, {"title": text})
        except ShopifyError as e:
            services.db.log_audit(user_id, "article_edit", gid, "error", str(e))
            await update.effective_message.reply_text(f"❌ Shopify error: {e}")
            return
        services.db.log_audit(user_id, "article_edit", gid, "ok", f"title={text[:80]}")
        await update.effective_message.reply_text("✅ Title changed.",
                                                  reply_markup=blog_menu_keyboard())
    elif step == "blog:extbody":
        gid = ctx.get("article_gid")
        services.db.set_step(user_id, None, ctx)
        await update.effective_message.reply_text("✍️ Claude is revising …")
        try:
            article = await services.shopify.get_article(gid)
            new_body = await services.claude.revise_article(article["body"], text)
            await services.shopify.update_article(gid, {"body": new_body})
        except (ClaudeError, ShopifyError) as e:
            services.db.log_audit(user_id, "article_edit", gid, "error", str(e))
            await update.effective_message.reply_text(f"❌ Error: {e}")
            return
        services.db.log_audit(user_id, "article_edit", gid, "ok", text[:200])
        await update.effective_message.reply_text("✅ Article revised.",
                                                  reply_markup=blog_menu_keyboard())


# --- callbacks ---------------------------------------------------------------

@authorized
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    query = update.callback_query
    user_id = update.effective_user.id
    action, arg = parse_cb(query.data)
    await query.answer()

    if action == "menu":
        services.db.set_step(user_id, None, {})
        await query.edit_message_text("📝 Blog — choose an action:",
                                      reply_markup=blog_menu_keyboard())
        return

    if action == "new":
        services.db.set_step(user_id, "blog:topic", {})
        await query.edit_message_text(
            "1/3 — Which awkward wall situation / topic?")
        return

    if action == "design":
        session = services.db.get_session(user_id)
        ctx = session["context"]
        # The value goes into the German drafting prompt, so "none" maps to
        # its German phrasing even though the button label is English.
        if arg in DESIGNS and arg != "none":
            ctx["design"] = DESIGNS[arg]
        else:
            ctx["design"] = "kein bestimmtes Design"
        services.db.set_step(user_id, "blog:must", ctx)
        await query.edit_message_text(
            "3/3 — Anything that must be included? (\"-\" for none)")
        return

    if action in ("listedit", "listdel"):
        try:
            _, articles = await services.shopify.list_articles(
                services.config.blog_id, first=10)
        except ShopifyError as e:
            await query.edit_message_text(f"❌ Shopify error: {e}")
            return
        if not articles:
            await query.edit_message_text("No articles found.",
                                          reply_markup=blog_menu_keyboard())
            return
        prefix = "pickedit" if action == "listedit" else "pickdel"
        verb = "edit" if action == "listedit" else "delete"
        await query.edit_message_text(f"Which article to {verb}?",
                                      reply_markup=article_list_keyboard(articles, prefix))
        return

    if action == "pickedit":
        await query.edit_message_text(
            "What would you like to change?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Change title",
                                      callback_data=f"blog:exttitle:{arg}")],
                [InlineKeyboardButton("Revise with Claude",
                                      callback_data=f"blog:extbody:{arg}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="blog:listedit")],
            ]))
        return

    if action in ("exttitle", "extbody"):
        session = services.db.get_session(user_id)
        ctx = session["context"]
        ctx["article_gid"] = article_gid(arg)
        services.db.set_step(user_id, f"blog:{action}", ctx)
        prompt = ("✏️ New title?" if action == "exttitle"
                  else "✏️ What should Claude change? (free-form instruction)")
        await query.edit_message_text(prompt)
        return

    if action == "pickdel":
        await query.edit_message_text(
            "⚠️ Really delete? This cannot be undone.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Yes, delete permanently",
                                      callback_data=f"blog:confdel:{arg}")],
                [InlineKeyboardButton("⬅️ Cancel", callback_data="blog:listdel")],
            ]))
        return

    if action == "confdel":
        gid = article_gid(arg)
        try:
            await services.shopify.delete_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "article_delete", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify error: {e}")
            return
        services.db.log_audit(user_id, "article_delete", gid, "ok", "")
        await query.edit_message_text("🗑 Article deleted.",
                                      reply_markup=blog_menu_keyboard())
        return

    # --- draft actions: arg is the draft id ---
    draft = services.db.get_draft(arg) if arg else None
    if action in ("pub", "regen", "title", "editdraft", "discard"):
        if draft is None or not draft.get("shopify_article_gid"):
            await query.edit_message_text("⚠️ This draft no longer exists.",
                                          reply_markup=blog_menu_keyboard())
            return
        gid = draft["shopify_article_gid"]

    if action == "pub":
        try:
            title = chosen_title(draft)
            if title != draft["title_a"]:
                await services.shopify.update_article(gid, {"title": title})
            article = await services.shopify.publish_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "publish", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify error: {e}")
            return
        services.db.update_draft(arg, status="published")
        services.db.log_audit(user_id, "publish", gid, "ok", article["handle"])
        try:
            blog_handle, _ = await services.shopify.list_articles(
                services.config.blog_id, first=1)
            live = services.shopify.live_url(blog_handle, article["handle"])
        except ShopifyError:
            live = services.shopify.admin_url(gid)
        await query.edit_message_text(
            f"✅ Published: *{md_escape(chosen_title(draft))}*\n{live}",
            parse_mode="Markdown")

    elif action == "regen":
        session = services.db.get_session(user_id)
        answers = session["context"]
        if not answers.get("topic"):
            await query.edit_message_text(
                "⚠️ Original inputs are missing — please start over.",
                reply_markup=blog_menu_keyboard())
            return
        try:
            await services.shopify.delete_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "regen", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify error: {e}")
            return
        services.db.delete_draft(arg)
        services.db.log_audit(user_id, "regen", gid, "ok", "old draft deleted")
        await query.edit_message_text("🔄 Old draft deleted.")
        await _create_and_preview(update, context, answers, user_id)

    elif action == "title":
        new_choice = "b" if draft["chosen_title"] == "a" else "a"
        services.db.update_draft(arg, chosen_title=new_choice)
        draft = services.db.get_draft(arg)
        try:
            await services.shopify.update_article(gid, {"title": chosen_title(draft)})
        except ShopifyError as e:
            await query.edit_message_text(f"❌ Shopify error: {e}")
            return
        services.db.log_audit(user_id, "title_toggle", gid, "ok", new_choice)
        await query.edit_message_text(
            preview_text(draft, services.shopify.admin_url(gid), []),
            reply_markup=preview_keyboard(arg), parse_mode="Markdown",
            disable_web_page_preview=True)

    elif action == "editdraft":
        session = services.db.get_session(user_id)
        ctx = session["context"]
        ctx["draft_id"] = arg
        services.db.set_step(user_id, "blog:editdraft", ctx)
        await query.edit_message_text(
            "✏️ What should Claude change? (free-form instruction)")

    elif action == "discard":
        try:
            await services.shopify.delete_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "discard", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify error: {e}")
            return
        services.db.delete_draft(arg)
        services.db.log_audit(user_id, "discard", gid, "ok", "")
        await query.edit_message_text("🗑 Draft discarded.",
                                      reply_markup=blog_menu_keyboard())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("📝 Blog — choose an action:",
                                              reply_markup=blog_menu_keyboard())


def register(app):
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^blog:"))
