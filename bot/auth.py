import functools


def is_allowlisted(config, user_id: int) -> bool:
    return user_id in config.allowlist_user_ids


def authorized(handler):
    """Gate a PTB handler: silent drop (audit-logged) for non-allowlisted users."""
    @functools.wraps(handler)
    async def wrapper(update, context):
        services = context.bot_data["services"]
        user = update.effective_user
        if user is None:
            return
        if not is_allowlisted(services.config, user.id):
            services.db.log_audit(user.id, "access", "-", "denied", "not allowlisted")
            return
        return await handler(update, context)
    return wrapper
