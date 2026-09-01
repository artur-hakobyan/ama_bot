import html as html_lib
import logging
from html.parser import HTMLParser

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.auth import authorized
from bot.claude_client import (ClaudeError, classify_feedback,
                               propose_articles)
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


def preview_text(draft: dict, admin_url, issues: list) -> str:
    lines = [
        f"📄 *{md_escape(chosen_title(draft))}*",
        "",
        md_escape(draft["summary"] or ""),
        "",
        f"Tags: {md_escape(', '.join(draft['tags']))}" if draft["tags"] else "",
        f"Admin: {admin_url}" if admin_url else "",
        "",
        "Status: draft (unpublished)" if admin_url
        else "Status: local draft — Shopify not connected yet",
    ]
    if issues:
        lines += ["", "⚠️ Self-check:"] + [f"• {md_escape(i)}" for i in issues]
    return "\n".join(line for line in lines if line is not None)


class _TelegramHTML(HTMLParser):
    """Convert article HTML (<p>, <h2>, <ul>/<ol>, <strong>…) to Telegram-safe HTML."""

    def __init__(self):
        super().__init__()
        self.out = []
        self._ol_index = None

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n<b>")
        elif tag == "p":
            self.out.append("\n\n")
        elif tag in ("strong", "b"):
            self.out.append("<b>")
        elif tag in ("em", "i"):
            self.out.append("<i>")
        elif tag == "ol":
            self._ol_index = 1
        elif tag == "li":
            if self._ol_index is None:
                self.out.append("\n• ")
            else:
                self.out.append(f"\n{self._ol_index}. ")
                self._ol_index += 1
        elif tag == "br":
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4"):
            self.out.append("</b>\n")
        elif tag in ("strong", "b"):
            self.out.append("</b>")
        elif tag in ("em", "i"):
            self.out.append("</i>")
        elif tag == "ol":
            self._ol_index = None

    def handle_data(self, data):
        self.out.append(html_lib.escape(data))


def html_to_telegram(body_html: str) -> str:
    parser = _TelegramHTML()
    parser.feed(body_html)
    text = "".join(parser.out)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def chunk_text(text: str, limit: int = 3900) -> list:
    """Split on paragraph boundaries to stay under Telegram's 4096-char cap."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def _send_article_body(msg, body_html: str):
    for chunk in chunk_text(html_to_telegram(body_html)):
        await msg.reply_text(chunk, parse_mode="HTML",
                             disable_web_page_preview=True)


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
        [InlineKeyboardButton("📊 From keyword sheet", callback_data="blog:pillars")],
        [InlineKeyboardButton("▶️ Run weekly batch now", callback_data="blog:batch")],
        [InlineKeyboardButton("✏️ Edit existing", callback_data="blog:listedit")],
        [InlineKeyboardButton("🗑 Delete existing", callback_data="blog:listdel")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main:menu")],
    ])


def pillar_keyboard(sheet, used: set) -> InlineKeyboardMarkup:
    """One row per pillar, showing how many keywords remain unwritten."""
    rows = []
    for i, pillar in enumerate(sheet.pillars):
        left = sheet.remaining(pillar, used)
        if left:
            rows.append([InlineKeyboardButton(f"{pillar} ({left})",
                                              callback_data=f"blog:pillar:{i}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="blog:menu")])
    return InlineKeyboardMarkup(rows)


def keyword_confirm_keyboard(pillar_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Write this article",
                              callback_data=f"blog:writekw:{pillar_index}")],
        [InlineKeyboardButton("⏭ Next keyword",
                              callback_data=f"blog:skipkw:{pillar_index}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="blog:pillars")],
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
    if services.config.shopify_enabled:
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
        admin_url = services.shopify.admin_url(article["id"])
    else:
        services.db.log_audit(user_id, "article_create", "-", "ok",
                              f"draft {draft_id} (local only, Shopify off)")
        admin_url = None
    draft = services.db.get_draft(draft_id)
    if admin_url is None:
        await _send_article_body(msg, draft["body_html"])
    await msg.reply_text(
        preview_text(draft, admin_url, check["issues"]),
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
        gid = draft.get("shopify_article_gid")
        try:
            new_body = await services.claude.revise_article(draft["body_html"], text)
            if gid:
                await services.shopify.update_article(gid, {"body": new_body})
        except (ClaudeError, ShopifyError) as e:
            services.db.log_audit(user_id, "article_edit", gid or "-", "error", str(e))
            await update.effective_message.reply_text(f"❌ Error: {e}")
            return
        services.db.update_draft(draft_id, body_html=new_body)
        services.db.log_audit(user_id, "article_edit", gid or "-", "ok", text[:200])

        # The learning loop: a correction meant as a general rule is remembered
        # for every future article, not just applied to this one.
        if services.rules is not None:
            try:
                verdict = await classify_feedback(services.claude, text)
                if verdict.get("is_general_rule") and verdict.get("rule_text"):
                    services.rules.add(verdict["rule_text"], source=text[:120])
                    services.db.log_audit(user_id, "rule_added", "-", "ok",
                                          verdict["rule_text"][:200])
                    await update.effective_message.reply_text(
                        "📌 Saved as a permanent rule for future articles:\n"
                        f"„{verdict['rule_text']}“\n\n"
                        "Use /rules to review or remove it.")
            except ClaudeError:
                pass  # a failed classification must never block the edit itself
        draft = services.db.get_draft(draft_id)
        admin_url = services.shopify.admin_url(gid) if gid else None
        if admin_url is None:
            await _send_article_body(update.effective_message, draft["body_html"])
        await update.effective_message.reply_text(
            preview_text(draft, admin_url, []),
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



# --- keyword-sheet flow -------------------------------------------------------

async def _write_from_keyword(update, context, pillar: str, kw, user_id: int,
                              proposal: dict = None, batch_id: str = None):
    """Run the three-pass SEO pipeline and file the result as a Shopify draft."""
    services = context.bot_data["services"]
    msg = update.effective_message
    status = await msg.reply_text(f"✍️ Writing \u201e{kw.keyword}\u201c …")

    if proposal and proposal.get("supporting_keywords"):
        supporting = proposal["supporting_keywords"][:8]
    else:
        ranked = services.keywords.ranked(pillar, services.db.used_keywords())
        supporting = [k.keyword for k in ranked if k.keyword != kw.keyword][:8]

    # Only link to articles that are actually live, so links never 404.
    links = []
    if services.config.shopify_enabled:
        try:
            handle, existing = await services.shopify.list_articles(
                services.config.blog_id, first=10)
            links = [(a["title"], f"https://amawalls.com/blogs/{handle}/{a['handle']}")
                     for a in existing if a.get("isPublished")][:6]
        except ShopifyError:
            links = []

    async def progress(label, findings):
        try:
            await status.edit_text(f"✍️ „{kw.keyword}“ — {label}")
        except Exception:
            pass  # editing is cosmetic; never fail the run over it

    try:
        draft_data, findings = await services.writer.write(
            kw.keyword, pillar, supporting, internal_links=links,
            on_progress=progress)
    except ClaudeError as e:
        services.db.log_audit(user_id, "seo_draft", kw.keyword, "error", str(e))
        await msg.reply_text(f"❌ Claude error: {e}")
        return

    draft_id = services.db.create_draft(
        user_id, draft_data["title_a"], draft_data["title_b"],
        draft_data["body_html"], draft_data["summary"], draft_data["tags"])
    ctx = services.db.get_session(user_id)["context"]
    ctx.update({"keyword": kw.keyword, "pillar": pillar})
    if batch_id:
        ctx["batch_id"] = batch_id
    services.db.set_step(user_id, None, ctx)

    admin_url = None
    if services.config.shopify_enabled:
        try:
            article = await services.shopify.create_article(
                services.config.blog_id, draft_data["title_a"],
                draft_data["body_html"], draft_data["summary"],
                draft_data["tags"], services.config.author_name)
            services.db.update_draft(draft_id, shopify_article_gid=article["id"])
            admin_url = services.shopify.admin_url(article["id"])
            services.db.log_audit(user_id, "seo_draft", kw.keyword, "ok",
                                  f"draft {draft_id}")
        except ShopifyError as e:
            services.db.log_audit(user_id, "seo_draft", kw.keyword, "error", str(e))
            await msg.reply_text(f"❌ Shopify error: {e}")

    from bot import style_check
    words = style_check.word_count(draft_data["body_html"])
    density = style_check.keyword_density(draft_data["body_html"], kw.keyword)
    draft = services.db.get_draft(draft_id)

    header = (f"🔑 {md_escape(kw.keyword)} · {words} Wörter · Dichte {density}%\n"
              f"📂 {md_escape(pillar)}")

    # Facts the writer was unsure about: a human must verify these before publishing.
    uncertain = draft_data.get("uncertain_facts") or []
    if uncertain:
        header += ("\n\n⚠️ *Please verify:*\n"
                   + "\n".join(f"• {md_escape(str(u))}" for u in uncertain[:6]))
    await msg.reply_text(
        header + "\n\n" + preview_text(draft, admin_url, findings),
        reply_markup=preview_keyboard(draft_id), parse_mode="Markdown",
        disable_web_page_preview=True)

    await _offer_images(msg, services, draft_id, kw.keyword, pillar)


async def _show_keyword(query, services, pillar_index: int, offset: int = 0):
    pillar = services.keywords.pillars[pillar_index]
    used = services.db.used_keywords()
    ranked = services.keywords.ranked(pillar, used)
    if not ranked:
        await query.edit_message_text(
            f"✅ „{pillar}“ is fully covered — every keyword has an article.",
            reply_markup=blog_menu_keyboard())
        return None, None
    kw = ranked[min(offset, len(ranked) - 1)]
    await query.edit_message_text(
        f"📂 *{md_escape(pillar)}*\n\n"
        f"Next keyword ({offset + 1}/{len(ranked)}):\n"
        f"🔑 *{md_escape(kw.keyword)}*\n"
        f"📈 {kw.volume:,}/Monat · Wettbewerb: {kw.competition or 'unbekannt'}"
        .replace(",", "."),
        reply_markup=keyword_confirm_keyboard(pillar_index), parse_mode="Markdown")
    return pillar, kw


async def _offer_images(msg, services, draft_id: str, keyword: str, pillar: str):
    """Show 2-3 matching mockups, or offer to generate one when none fit."""
    from bot.google_drive import image_brief, suggest_images

    if not services.google or not services.config.drive_mockups_folder_id:
        return
    try:
        images = services.images_cached()
        picks = suggest_images(images, [keyword, pillar])
    except Exception as e:                      # Drive hiccup must not block review
        logger.warning("Image lookup failed: %s", e)
        return

    if not picks:
        await msg.reply_text(
            "🖼 No suitable mockup found in _All Mockups.\n\n"
            f"Suggested image to create:\n_{md_escape(image_brief(keyword, pillar))}_",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "🎨 Generate an image", callback_data=f"blog:genimg:{draft_id}")]]),
            parse_mode="Markdown")
        return

    from telegram import InputMediaPhoto
    media, buttons = [], []
    for n, pick in enumerate(picks, 1):
        try:
            data = services.google.download(pick["id"])
        except Exception:
            continue
        media.append(InputMediaPhoto(data, caption=f"{n}. {pick['name'][:180]}"))
        buttons.append([InlineKeyboardButton(f"✅ Use image {n}",
                                             callback_data=f"blog:useimg:{draft_id}:{n}")])
    if not media:
        return
    await msg.reply_media_group(media)
    buttons.append([InlineKeyboardButton("🎨 Generate instead",
                                         callback_data=f"blog:genimg:{draft_id}")])
    await msg.reply_text("🖼 Matching mockups — which one for the article?",
                         reply_markup=InlineKeyboardMarkup(buttons))


# --- weekly batch -------------------------------------------------------------

BATCH_SIZE = 3


def proposals_text(proposals: list, pillar: str) -> str:
    lines = [f"📋 *{md_escape(pillar)}* — {len(proposals)} proposals\n"]
    for n, p in enumerate(proposals, 1):
        lines.append(
            f"*{n}. {md_escape(p['title'])}*\n"
            f"🔑 {md_escape(p['keyword'])}\n"
            f"_{md_escape(p.get('value', ''))}_\n"
            + "\n".join(f"  • {md_escape(o)}" for o in p.get("outline", [])[:6]))
    return "\n\n".join(lines)


def batch_keyboard(batch_id: str, count: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("✅ Approve all & write",
                                  callback_data=f"blog:bstart:{batch_id}")]]
    rows.append([InlineKeyboardButton(f"🔄 Replace #{n}",
                                      callback_data=f"blog:bswap:{batch_id}:{n}")
                 for n in range(1, count + 1)])
    rows.append([InlineKeyboardButton("❌ Cancel",
                                      callback_data=f"blog:bcancel:{batch_id}")])
    return InlineKeyboardMarkup(rows)


async def _start_batch(update, context, user_id: int, pillar: str = None):
    """Pick the next keywords and propose three articles for approval."""
    services = context.bot_data["services"]
    msg = update.effective_message
    if not services.keywords:
        await msg.reply_text("⚠️ Keyword sheet not available.")
        return

    pillar = pillar or services.keywords.pillars[0]
    used = services.db.used_keywords()
    ranked = services.keywords.ranked(pillar, used)
    if not ranked:
        await msg.reply_text(f"✅ \u201e{pillar}\u201c is fully covered.",
                             reply_markup=blog_menu_keyboard())
        return

    picks = ranked[:BATCH_SIZE]
    status = await msg.reply_text(
        f"📋 Creating {len(picks)} proposals for \u201e{pillar}\u201c …")
    try:
        proposals = await propose_articles(
            services.claude, picks, pillar,
            services.rules.as_prompt_block() if services.rules else "")
    except ClaudeError as e:
        await status.edit_text(f"❌ Claude error: {e}")
        return
    if not proposals:
        await status.edit_text("❌ No proposals returned — please try again.")
        return

    batch_id = services.db.create_batch(user_id, pillar, proposals)
    services.db.log_audit(user_id, "batch_proposed", pillar, "ok",
                          f"{len(proposals)} proposals")
    await status.edit_text(proposals_text(proposals, pillar),
                           reply_markup=batch_keyboard(batch_id, len(proposals)),
                           parse_mode="Markdown")


async def _run_next_in_batch(update, context, batch_id: str, user_id: int):
    """Write the next approved article. Sequential: one at a time, by design —
    the operator should never have two drafts awaiting review."""
    services = context.bot_data["services"]
    batch = services.db.get_batch(batch_id)
    if batch is None:
        return
    proposals = batch["proposals"]
    index = batch["current_index"]

    if index >= len(proposals):
        services.db.update_batch(batch_id, status="done")
        await update.effective_message.reply_text(
            f"🎉 Batch complete — {len(proposals)} articles processed.",
            reply_markup=blog_menu_keyboard())
        return

    proposal = proposals[index]
    kw = _KeywordLike(proposal["keyword"])
    await update.effective_message.reply_text(
        f"✍️ Article {index + 1}/{len(proposals)}: *{md_escape(proposal['title'])}*",
        parse_mode="Markdown")
    services.db.update_batch(batch_id, status="running")
    await _write_from_keyword(update, context, batch["pillar"], kw, user_id,
                              proposal=proposal, batch_id=batch_id)


async def scheduled_batch(context, user_id: int):
    """Weekly entry point. Unlike the button flow there is no message to reply
    to, so it sends a fresh one and picks the pillar with the most keywords left.
    """
    services = context.bot_data["services"]
    if not services.keywords:
        return
    if services.db.active_batch(user_id):
        logger.info("Skipping weekly batch for %s — one is still open", user_id)
        return

    used = services.db.used_keywords()
    pillars = [(services.keywords.remaining(p, used), p)
               for p in services.keywords.pillars]
    pillars = [(n, p) for n, p in pillars if n]
    if not pillars:
        await context.bot.send_message(
            user_id, "⚠️ All keywords are used up — please add more to the sheet.")
        return
    remaining, pillar = max(pillars)

    sent = await context.bot.send_message(
        user_id, f"🗓 Weekly batch: creating proposals for \u201e{pillar}\u201c …")

    class _Shim:                      # gives _start_batch something to reply to
        effective_message = sent

    await _start_batch(_Shim(), context, user_id, pillar)

    if remaining <= BATCH_SIZE * 2:   # warn before a pillar runs dry
        await context.bot.send_message(
            user_id,
            f"ℹ️ \u201e{pillar}\u201c has only {remaining} keywords left. "
            "Please top up the sheet soon.")


class _KeywordLike:
    """Minimal stand-in so batch articles reuse the single-article writer."""

    def __init__(self, keyword: str, volume: int = 0, competition: str = ""):
        self.keyword = keyword
        self.volume = volume
        self.competition = competition


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

    if action == "batch":
        await query.edit_message_text("▶️ Starting weekly batch …")
        await _start_batch(update, context, user_id)
        return

    if action == "bstart":
        batch = services.db.get_batch(arg)
        if batch is None:
            await query.edit_message_text("⚠️ This batch no longer exists.",
                                          reply_markup=blog_menu_keyboard())
            return
        services.db.log_audit(user_id, "batch_approved", arg, "ok", "")
        await query.edit_message_text(
            f"✅ Approved — writing {len(batch['proposals'])} articles one after another.")
        await _run_next_in_batch(update, context, arg, user_id)
        return

    if action == "bswap":
        parts = (arg or "").split(":")
        batch = services.db.get_batch(parts[0]) if parts else None
        if batch is None or len(parts) < 2:
            await query.edit_message_text("⚠️ This batch no longer exists.",
                                          reply_markup=blog_menu_keyboard())
            return
        n = int(parts[1]) - 1
        proposals = batch["proposals"]
        # Swap in the next unused keyword that isn't already in this batch.
        chosen = {p["keyword"].lower() for p in proposals}
        used = services.db.used_keywords() | chosen
        ranked = services.keywords.ranked(batch["pillar"], used)
        if not ranked or not (0 <= n < len(proposals)):
            await query.answer("No further keyword available", show_alert=True)
            return
        await query.edit_message_text("🔄 Creating replacement proposal …")
        try:
            new = await propose_articles(
                services.claude, ranked[:1], batch["pillar"],
                services.rules.as_prompt_block() if services.rules else "")
        except ClaudeError as e:
            await query.edit_message_text(f"❌ Claude error: {e}")
            return
        if new:
            proposals[n] = new[0]
            import json as _json
            services.db.update_batch(parts[0],
                                     proposals_json=_json.dumps(proposals))
        await query.edit_message_text(
            proposals_text(proposals, batch["pillar"]),
            reply_markup=batch_keyboard(parts[0], len(proposals)),
            parse_mode="Markdown")
        return

    if action == "bcancel":
        services.db.update_batch(arg, status="cancelled")
        services.db.log_audit(user_id, "batch_cancelled", arg, "ok", "")
        await query.edit_message_text("❌ Batch cancelled.",
                                      reply_markup=blog_menu_keyboard())
        return

    if action in ("useimg", "genimg"):
        # arg is "draftid" (genimg) or "draftid:n" (useimg)
        parts = (arg or "").split(":")
        draft = services.db.get_draft(parts[0]) if parts else None
        if draft is None:
            await query.edit_message_text("⚠️ This draft no longer exists.",
                                          reply_markup=blog_menu_keyboard())
            return
        if action == "useimg":
            # Shopify's article image takes a public URL; Drive links are not
            # publicly served, so the operator attaches it in the admin for now.
            await query.edit_message_text(
                f"🖼 Bild {parts[1] if len(parts) > 1 else ''} vorgemerkt.\n"
                "Lade es im Shopify-Admin als Beitragsbild hoch — "
                "der Entwurf ist dort bereits angelegt.")
        else:
            services.db.log_audit(user_id, "image_generate_requested",
                                  draft["id"], "ok", "")
            await query.edit_message_text(
                "🎨 Bildgenerierung ist noch nicht angeschlossen.\n"
                "Der Vorschlag oben beschreibt das passende Motiv — "
                "ich baue die automatische Generierung als Nächstes ein.")
        return

    if action == "pillars":
        if not services.keywords:
            await query.edit_message_text(
                "⚠️ Keyword sheet not available on this installation.",
                reply_markup=blog_menu_keyboard())
            return
        used = services.db.used_keywords()
        await query.edit_message_text(
            "📊 Choose a pillar topic (remaining keywords in brackets):",
            reply_markup=pillar_keyboard(services.keywords, used))
        return

    if action == "pillar":
        ctx = services.db.get_session(user_id)["context"]
        ctx["kw_offset"] = 0
        services.db.set_step(user_id, None, ctx)
        await _show_keyword(query, services, int(arg), 0)
        return

    if action == "skipkw":
        ctx = services.db.get_session(user_id)["context"]
        offset = int(ctx.get("kw_offset", 0)) + 1
        ctx["kw_offset"] = offset
        services.db.set_step(user_id, None, ctx)
        await _show_keyword(query, services, int(arg), offset)
        return

    if action == "writekw":
        pillar_index = int(arg)
        pillar = services.keywords.pillars[pillar_index]
        ctx = services.db.get_session(user_id)["context"]
        offset = int(ctx.get("kw_offset", 0))
        ranked = services.keywords.ranked(pillar, services.db.used_keywords())
        if not ranked:
            await query.edit_message_text("⚠️ No keywords left in this pillar.",
                                          reply_markup=blog_menu_keyboard())
            return
        kw = ranked[min(offset, len(ranked) - 1)]
        await query.edit_message_text(f"🔑 {kw.keyword} — starting …")
        await _write_from_keyword(update, context, pillar, kw, user_id)
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
        if not services.config.shopify_enabled:
            await query.edit_message_text(
                "📦 Shopify integration will be ready soon — managing existing "
                "store articles will be available then.",
                reply_markup=blog_menu_keyboard())
            return
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
        if draft is None or (services.config.shopify_enabled
                             and not draft.get("shopify_article_gid")):
            await query.edit_message_text("⚠️ This draft no longer exists.",
                                          reply_markup=blog_menu_keyboard())
            return
        gid = draft.get("shopify_article_gid")

    if action == "pub":
        if not gid:
            services.db.log_audit(user_id, "publish", "-", "skipped", "Shopify off")
            await update.effective_message.reply_text(
                "📦 Shopify integration will be ready soon — this article will "
                "be published to the store then. For now it stays saved as a "
                "local draft.")
            return
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

        # A published keyword is spent: never propose it again.
        ctx = services.db.get_session(user_id)["context"]
        if ctx.get("keyword"):
            services.db.mark_keyword_used(ctx["keyword"], ctx.get("pillar", ""), gid)
        try:
            blog_handle, _ = await services.shopify.list_articles(
                services.config.blog_id, first=1)
            live = services.shopify.live_url(blog_handle, article["handle"])
        except ShopifyError:
            live = services.shopify.admin_url(gid)
        await query.edit_message_text(
            f"✅ Published: *{md_escape(chosen_title(draft))}*\n{live}",
            parse_mode="Markdown")

        # Sequential batch: the next article starts only now, so the operator
        # never has two drafts waiting at once.
        batch_id = ctx.get("batch_id")
        if batch_id:
            batch = services.db.get_batch(batch_id)
            if batch and batch["status"] == "running":
                services.db.update_batch(batch_id,
                                         current_index=batch["current_index"] + 1)
                await _run_next_in_batch(update, context, batch_id, user_id)

    elif action == "regen":
        session = services.db.get_session(user_id)
        answers = session["context"]
        if not answers.get("topic"):
            await query.edit_message_text(
                "⚠️ Original inputs are missing — please start over.",
                reply_markup=blog_menu_keyboard())
            return
        if gid:
            try:
                await services.shopify.delete_article(gid)
            except ShopifyError as e:
                services.db.log_audit(user_id, "regen", gid, "error", str(e))
                await query.edit_message_text(f"❌ Shopify error: {e}")
                return
        services.db.delete_draft(arg)
        services.db.log_audit(user_id, "regen", gid or "-", "ok", "old draft deleted")
        await query.edit_message_text("🔄 Old draft deleted.")
        await _create_and_preview(update, context, answers, user_id)

    elif action == "title":
        new_choice = "b" if draft["chosen_title"] == "a" else "a"
        services.db.update_draft(arg, chosen_title=new_choice)
        draft = services.db.get_draft(arg)
        if gid:
            try:
                await services.shopify.update_article(gid, {"title": chosen_title(draft)})
            except ShopifyError as e:
                await query.edit_message_text(f"❌ Shopify error: {e}")
                return
        services.db.log_audit(user_id, "title_toggle", gid or "-", "ok", new_choice)
        await query.edit_message_text(
            preview_text(draft, services.shopify.admin_url(gid) if gid else None, []),
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
        if gid:
            try:
                await services.shopify.delete_article(gid)
            except ShopifyError as e:
                services.db.log_audit(user_id, "discard", gid, "error", str(e))
                await query.edit_message_text(f"❌ Shopify error: {e}")
                return
        services.db.delete_draft(arg)
        services.db.log_audit(user_id, "discard", gid or "-", "ok", "")
        await query.edit_message_text("🗑 Draft discarded.",
                                      reply_markup=blog_menu_keyboard())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("📝 Blog — choose an action:",
                                              reply_markup=blog_menu_keyboard())


def register(app):
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^blog:"))
