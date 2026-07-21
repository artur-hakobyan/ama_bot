---
name: amma-bot-rules
description: Golden rules for the AMAwalls Ops Bot repo. Use this skill BEFORE writing or changing ANY code in this repository — new features, new modules, bug fixes, refactors, config or deploy changes, anything touching Shopify, Telegram, Claude/Anthropic calls, secrets, or the database. Also consult it when reviewing code or answering questions about how this bot should behave.
---

# AMAwalls Ops Bot — Golden Rules

These rules come from the project owner and are non-negotiable. They exist because this bot operates a real Shopify store: a leaked secret, an accidental publish, or an unaudited action has real-world consequences.

## The rules

1. **No hardcoded secrets, ever.** Every sensitive value (bot token, Anthropic key, Shopify token) is read from the gitignored `.env`. When adding a new config value: add it to `.env.example` with a comment and empty value, add it to `bot/config.py` (`Config.load` fails fast listing missing required keys), and document it in the README table. Never print or log secrets — note that `logging.getLogger("httpx")` is pinned to WARNING precisely because httpx leaks the bot token at INFO.

2. **Shopify Admin GraphQL only.** All Shopify calls go through `bot/shopify_client.py` hitting `/admin/api/{version}/graphql.json` with `X-Shopify-Access-Token`. REST is legacy — do not use it, and do not scatter Shopify HTTP calls outside the client. Every mutation checks `userErrors` and raises `ShopifyError` with the messages; handlers surface these verbatim in chat.

3. **Draft-first + explicit approval.** Anything that changes the live store is created as a draft and goes live only on a human button tap. `ShopifyClient.create_article` hardcodes `isPublished: False` — keep that structural (not a parameter), so publishing is impossible except via the explicit Publish action. Destructive actions (delete) require a separate confirmation button before executing.

4. **Dev store first.** Build and test against the development store. Going live must remain nothing more than swapping `SHOPIFY_STORE_DOMAIN` + `SHOPIFY_ADMIN_TOKEN` (+ `BLOG_ID`) in `.env` — never introduce code that branches on "prod vs dev".

5. **Allowlist auth on every handler.** The Telegram user-ID allowlist is the only gate (owner decision 2026-07-21: the original password layer was removed; three team members use the bot). Non-allowlisted users get **no response at all** (silent drop, but audit-logged). Every new handler must be gated (`@authorized` from `bot/auth.py`, or the inline check pattern in `bot/main.py`) — an ungated handler is an auth bypass. New members = add their numeric ID to `ALLOWLIST_USER_IDS` in `.env` and restart.

6. **Audit everything.** Every state-changing action writes `audit_log` (user id, action, target, result, detail) — on success AND error paths.

## Architecture invariants

- **Modules are self-contained.** A module = one file in `bot/modules/` exposing `NAME`, `MENU_LABEL`, `register(app)`, `handle_step(step, update, context)`, registered in the `modules=[...]` list in `bot/main.py:main()`. All its `callback_data` and session steps are namespaced `<name>:…`. Adding a module must not touch core files beyond that one list entry.
- **State lives in SQLite** (`bot/db.py`: sessions/drafts/audit_log), not in memory — the bot must survive restarts mid-conversation. Only short ids travel in `callback_data` (≤64 bytes); content is looked up in the drafts table.
- **User/Claude-generated text in Markdown messages must go through `md_escape`** (`bot/modules/blog.py`) — unescaped `*_[` in a title makes Telegram reject the whole message.

## Language split (owner decision, 2026-07-21)

**Bot UI is English; blog content is German.** All button labels, menus, prompts, status and error messages the operator sees in Telegram are English. Everything that ends up on the blog (articles, titles, summaries, tags) and everything passed into the Claude drafting prompts stays German — e.g. the "No specific design" button maps to the German value "kein bestimmtes Design" before it reaches the prompt. Keep new UI strings English and new content strings German.

Commands registered in Telegram's "/" menu: `/start` (unlock / main menu), `/menu` (main menu), `/stop` (cancel current flow — clears the session step). Any new command must be added to `_register_commands` in bot/main.py so it appears in the slash menu, and must be auth-gated like the others.

## Working practice

- TDD: run `python -m pytest` from the repo root; the suite must stay green and its output pristine.
- Before first use of new Shopify credentials, run `python scripts/smoke_test.py` (creates + deletes a throwaway draft; must print `SMOKE TEST PASSED`).
- Content language is German (`CONTENT_LANG=de`); brand voice lives in the system prompt in `bot/claude_client.py` — warm/organic/accessible-premium, problem-first about awkward wall spaces, informative not ad-like.
- Deploy is `./deploy/deploy.sh arturserver` (rsync + systemd); the server-side `.env` is filled manually and never synced.
