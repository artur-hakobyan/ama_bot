import logging

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.error import NetworkError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from bot.auth import is_allowlisted
from bot.claude_client import ClaudeClient, SEOWriter
from bot.config import Config
from bot.db import Database
from bot.house_rules import HouseRules
from bot.keywords import KeywordSheet
from bot.modules import blog
from bot.modules.registry import Services, main_menu_keyboard
from bot.shopify_client import ShopifyClient

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

MAIN_MENU_TEXT = "AMAwalls Ops — what would you like to do?"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    user = update.effective_user
    if user is None or not is_allowlisted(services.config, user.id):
        if user:
            services.db.log_audit(user.id, "access", "/start", "denied", "not allowlisted")
        return
    await update.effective_message.reply_text(
        MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(context.bot_data["modules"]))


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    user = update.effective_user
    if user is None or not is_allowlisted(services.config, user.id):
        if user:
            services.db.log_audit(user.id, "access", "message", "denied", "not allowlisted")
        return
    session = services.db.get_session(user.id)
    step = session["step"]
    if step and ":" in step:
        module_name = step.split(":", 1)[0]
        for mod in context.bot_data["modules"]:
            if mod.NAME == module_name:
                await mod.handle_step(step, update, context)
                return


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel whatever flow is in progress and return to the main menu."""
    services = context.bot_data["services"]
    user = update.effective_user
    if user is None or not is_allowlisted(services.config, user.id):
        if user:
            services.db.log_audit(user.id, "access", "/stop", "denied", "not allowlisted")
        return
    services.db.set_step(user.id, None, {})
    services.db.log_audit(user.id, "stop", "-", "ok", "flow cancelled")
    await update.effective_message.reply_text(
        "⏹ Stopped. " + MAIN_MENU_TEXT,
        reply_markup=main_menu_keyboard(context.bot_data["modules"]))


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the learned house rules; `/rules remove N` deletes one."""
    services = context.bot_data["services"]
    user = update.effective_user
    if user is None or not is_allowlisted(services.config, user.id):
        if user:
            services.db.log_audit(user.id, "access", "/rules", "denied", "not allowlisted")
        return
    args = context.args if hasattr(context, "args") else []
    rules = services.rules

    if args and args[0].lower() in ("remove", "delete", "del") and len(args) > 1:
        try:
            index = int(args[1])
        except ValueError:
            await update.effective_message.reply_text("Usage: /rules remove 2")
            return
        removed = rules.remove(index)
        if removed:
            services.db.log_audit(user.id, "rule_removed", "-", "ok",
                                  removed["text"][:200])
            await update.effective_message.reply_text(
                f"🗑 Removed rule {index}:\n„{removed['text']}“")
        else:
            await update.effective_message.reply_text(f"No rule number {index}.")
        return

    entries = rules.all()
    if not entries:
        await update.effective_message.reply_text(
            "No house rules yet.\n\nWhen you request a change during review, the bot "
            "decides whether it is a lasting rule and saves it here automatically.")
        return
    lines = [f"{i}. {r['text']}  _(added {r['added']})_"
             for i, r in enumerate(entries, 1)]
    await update.effective_message.reply_text(
        "📌 *House rules applied to every article:*\n\n" + "\n\n".join(lines) +
        "\n\nRemove one with `/rules remove <number>`.",
        parse_mode="Markdown")


async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Coming soon 🚧")


async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    services = context.bot_data["services"]
    user = update.effective_user
    if user is not None:
        services.db.set_step(user.id, None, {})
    await query.edit_message_text(
        MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(context.bot_data["modules"]))


async def error_handler(update, context):
    err = context.error
    # Polling drops (laptop sleep, wifi switch, VPN) are transient and
    # self-healing: log one line, don't spam the chat or a stack trace.
    if isinstance(err, NetworkError):
        logging.getLogger(__name__).warning("Network hiccup: %s", err)
        return
    logging.getLogger(__name__).error("Unhandled error", exc_info=err)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Unexpected error — please try again, or /start to reset.")
        except Exception:
            pass


async def _register_commands(app: Application):
    """Populate Telegram's "/" command menu (shown when the user types a slash)."""
    await app.bot.set_my_commands([
        BotCommand("start", "Unlock / show the main menu"),
        BotCommand("menu", "Show the main menu"),
        BotCommand("stop", "Cancel the current action"),
        BotCommand("rules", "Show or remove learned writing rules"),
    ])


def build_application(services: Services, modules) -> Application:
    app = (Application.builder()
           .token(services.config.telegram_bot_token)
           .post_init(_register_commands)
           .build())
    app.bot_data["services"] = services
    app.bot_data["modules"] = list(modules)
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("menu", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CallbackQueryHandler(noop_cb, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(main_menu_cb, pattern="^main:menu$"))
    for mod in modules:
        mod.register(app)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)
    return app


def main():
    load_dotenv()
    cfg = Config.load()
    services = Services(
        config=cfg,
        db=Database(cfg.db_path),
        shopify=ShopifyClient(cfg.shopify_store_domain, cfg.shopify_admin_token,
                              cfg.shopify_api_version,
                              client_id=cfg.shopify_client_id,
                              client_secret=cfg.shopify_client_secret),
        claude=ClaudeClient(cfg.anthropic_api_key, cfg.claude_model),
    )
    services.rules = HouseRules(cfg.house_rules_path)
    if cfg.google_credentials_path:
        try:
            from bot.google_drive import GoogleClient
            services.google = GoogleClient(cfg.google_credentials_path)
        except Exception as e:
            logging.getLogger(__name__).warning("Google access unavailable: %s", e)
    services.writer = SEOWriter(services.claude, services.rules)
    # The live sheet is authoritative — the operator edits it directly. The local
    # workbook is only a fallback when Google is unreachable.
    if services.google and cfg.keyword_spreadsheet_id:
        try:
            from bot.keywords import LiveKeywordSheet
            services.keywords = LiveKeywordSheet(services.google,
                                                 cfg.keyword_spreadsheet_id)
            logging.getLogger(__name__).info("Using live Google keyword sheet")
        except Exception as e:
            logging.getLogger(__name__).warning("Live sheet unavailable: %s", e)
    if services.keywords is None:
        try:
            services.keywords = KeywordSheet(cfg.keyword_sheet_path)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Keyword sheet unavailable (%s) — keyword flow disabled", e)
    app = build_application(services, modules=[blog])
    if cfg.transport == "webhook":
        app.run_webhook(listen="0.0.0.0", port=cfg.webhook_port,
                        url_path=cfg.telegram_bot_token,
                        webhook_url=f"{cfg.webhook_url}/{cfg.telegram_bot_token}")
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
