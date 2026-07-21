import pytest
from bot.config import Config, ConfigError

VALID_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "BOT_PASSWORD": "pw",
    "ALLOWLIST_USER_IDS": "111, 222",
    "ANTHROPIC_API_KEY": "sk-ant",
    "SHOPIFY_STORE_DOMAIN": "dev.myshopify.com",
    "SHOPIFY_ADMIN_TOKEN": "shpat_x",
    "SHOPIFY_API_VERSION": "2026-07",
    "BLOG_ID": "gid://shopify/Blog/1",
}

def test_load_valid():
    cfg = Config.load(VALID_ENV)
    assert cfg.allowlist_user_ids == frozenset({111, 222})
    assert cfg.transport == "polling"
    assert cfg.content_lang == "de"
    assert cfg.claude_model == "claude-sonnet-5"

def test_missing_keys_all_listed():
    with pytest.raises(ConfigError) as exc:
        Config.load({"TELEGRAM_BOT_TOKEN": "x"})
    assert "ANTHROPIC_API_KEY" in str(exc.value) and "BLOG_ID" in str(exc.value)

def test_bad_allowlist():
    with pytest.raises(ConfigError):
        Config.load({**VALID_ENV, "ALLOWLIST_USER_IDS": "abc"})

def test_webhook_requires_url():
    with pytest.raises(ConfigError):
        Config.load({**VALID_ENV, "TRANSPORT": "webhook"})


def test_shopify_enabled_flag():
    assert Config.load(VALID_ENV).shopify_enabled is True
    assert Config.load({**VALID_ENV, "SHOPIFY_ENABLED": "false"}).shopify_enabled is False
    assert Config.load({**VALID_ENV, "SHOPIFY_ENABLED": "0"}).shopify_enabled is False
