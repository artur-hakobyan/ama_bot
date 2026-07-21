import functools
import hmac

PASSWORD_PROMPT = "🔒 Bitte Passwort eingeben."


def is_allowlisted(config, user_id: int) -> bool:
    return user_id in config.allowlist_user_ids


def check_password(config, attempt: str) -> bool:
    return hmac.compare_digest(attempt.encode(), config.bot_password.encode())


def authorized(handler):
    """Gate a PTB handler: silent drop for non-allowlisted, password prompt if locked."""
    @functools.wraps(handler)
    async def wrapper(update, context):
        services = context.bot_data["services"]
        user = update.effective_user
        if user is None:
            return
        if not is_allowlisted(services.config, user.id):
            services.db.log_audit(user.id, "access", "-", "denied", "not allowlisted")
            return
        if not services.db.get_session(user.id)["unlocked"]:
            if update.effective_message:
                await update.effective_message.reply_text(PASSWORD_PROMPT)
            return
        return await handler(update, context)
    return wrapper
