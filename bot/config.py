import os
from dataclasses import dataclass
from typing import Mapping


class ConfigError(Exception):
    pass


REQUIRED_KEYS = [
    "TELEGRAM_BOT_TOKEN", "BOT_PASSWORD", "ALLOWLIST_USER_IDS",
    "ANTHROPIC_API_KEY", "SHOPIFY_STORE_DOMAIN", "SHOPIFY_ADMIN_TOKEN",
    "SHOPIFY_API_VERSION", "BLOG_ID",
]


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    bot_password: str
    allowlist_user_ids: frozenset
    anthropic_api_key: str
    shopify_store_domain: str
    shopify_admin_token: str
    shopify_api_version: str
    blog_id: str
    content_lang: str
    transport: str
    webhook_url: str
    webhook_port: int
    claude_model: str
    db_path: str
    author_name: str

    @classmethod
    def load(cls, env: Mapping = os.environ) -> "Config":
        missing = [k for k in REQUIRED_KEYS if not env.get(k)]
        if missing:
            raise ConfigError("Missing required .env keys: " + ", ".join(missing))
        try:
            ids = frozenset(int(p) for p in env["ALLOWLIST_USER_IDS"].split(",") if p.strip())
        except ValueError:
            raise ConfigError("ALLOWLIST_USER_IDS must be comma-separated integers")
        if not ids:
            raise ConfigError("ALLOWLIST_USER_IDS must not be empty")
        transport = env.get("TRANSPORT") or "polling"
        if transport not in ("polling", "webhook"):
            raise ConfigError("TRANSPORT must be 'polling' or 'webhook'")
        if transport == "webhook" and not env.get("WEBHOOK_URL"):
            raise ConfigError("WEBHOOK_URL is required when TRANSPORT=webhook")
        return cls(
            telegram_bot_token=env["TELEGRAM_BOT_TOKEN"],
            bot_password=env["BOT_PASSWORD"],
            allowlist_user_ids=ids,
            anthropic_api_key=env["ANTHROPIC_API_KEY"],
            shopify_store_domain=env["SHOPIFY_STORE_DOMAIN"],
            shopify_admin_token=env["SHOPIFY_ADMIN_TOKEN"],
            shopify_api_version=env["SHOPIFY_API_VERSION"],
            blog_id=env["BLOG_ID"],
            content_lang=env.get("CONTENT_LANG") or "de",
            transport=transport,
            webhook_url=env.get("WEBHOOK_URL") or "",
            webhook_port=int(env.get("WEBHOOK_PORT") or 8443),
            claude_model=env.get("CLAUDE_MODEL") or "claude-sonnet-5",
            db_path=env.get("DB_PATH") or "amma_bot.db",
            author_name=env.get("AUTHOR_NAME") or "AMAwalls Team",
        )
