"""Verify Shopify credentials + BLOG_ID: create a throwaway draft, then delete it.

Run from repo root:  python scripts/smoke_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from bot.config import Config, ConfigError
from bot.shopify_client import ShopifyClient, ShopifyError


async def main() -> int:
    load_dotenv()
    try:
        cfg = Config.load()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}")
        return 1

    shopify = ShopifyClient(cfg.shopify_store_domain, cfg.shopify_admin_token,
                            cfg.shopify_api_version)
    print(f"Store: {cfg.shopify_store_domain}  Blog: {cfg.blog_id}")

    try:
        handle, articles = await shopify.list_articles(cfg.blog_id, first=1)
        print(f"OK  blog found (handle: {handle}, {len(articles)} recent article(s))")

        art = await shopify.create_article(
            cfg.blog_id, "SMOKE TEST — bitte ignorieren",
            "<p>Wegwerf-Entwurf vom Smoke-Test.</p>", "Smoke test", [], cfg.author_name)
        assert art["isPublished"] is False, "draft came back published!"
        print(f"OK  draft created as unpublished: {art['id']}")

        deleted = await shopify.delete_article(art["id"])
        print(f"OK  draft deleted: {deleted}")
    except (ShopifyError, AssertionError) as e:
        print(f"SMOKE TEST FAILED: {e}")
        return 1

    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
