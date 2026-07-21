import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from bot.auth import PASSWORD_PROMPT, check_password, is_allowlisted
from bot.claude_client import ClaudeClient
from bot.config import Config
from bot.db import Database
from bot.modules import blog
from bot.modules.registry import Services, main_menu_keyboard
from bot.shopify_client import ShopifyClient

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

MAIN_MENU_TEXT = "AMAwalls Ops — was möchtest du tun?"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    user = update.effective_user
    if user is None or not is_allowlisted(services.config, user.id):
        if user:
            services.db.log_audit(user.id, "access", "/start", "denied", "not allowlisted")
        return
    if not services.db.get_session(user.id)["unlocked"]:
        await update.effective_message.reply_text(PASSWORD_PROMPT)
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
    text = (update.effective_message.text or "").strip()

    if not session["unlocked"]:
        if check_password(services.config, text):
            services.db.set_unlocked(user.id, True)
            services.db.log_audit(user.id, "auth", "-", "ok", "unlocked")
            await update.effective_message.reply_text(
                MAIN_MENU_TEXT,
                reply_markup=main_menu_keyboard(context.bot_data["modules"]))
        else:
            services.db.log_audit(user.id, "auth", "-", "failed", "wrong password")
            await update.effective_message.reply_text("❌ Falsches Passwort.")
        return

    step = session["step"]
    if step and ":" in step:
        module_name = step.split(":", 1)[0]
        for mod in context.bot_data["modules"]:
            if mod.NAME == module_name:
                await mod.handle_step(step, update, context)
                return


async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Bald verfügbar 🚧")


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
    logging.getLogger(__name__).error("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Unerwarteter Fehler — bitte /start.")
        except Exception:
            pass


def build_application(services: Services, modules) -> Application:
    app = Application.builder().token(services.config.telegram_bot_token).build()
    app.bot_data["services"] = services
    app.bot_data["modules"] = list(modules)
    app.add_handler(CommandHandler("start", start_cmd))
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
                              cfg.shopify_api_version),
        claude=ClaudeClient(cfg.anthropic_api_key, cfg.claude_model),
    )
    app = build_application(services, modules=[blog])
    if cfg.transport == "webhook":
        app.run_webhook(listen="0.0.0.0", port=cfg.webhook_port,
                        url_path=cfg.telegram_bot_token,
                        webhook_url=f"{cfg.webhook_url}/{cfg.telegram_bot_token}")
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
