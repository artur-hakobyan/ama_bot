# AMAwalls Ops Bot — Phase 1 (Blog) Design

Date: 2026-07-21
Status: Approved

## Purpose

A Telegram bot that is the command center for operating the AMAwalls Shopify store.
Phase 1: the Blog module — create, edit, and delete blog articles, with Claude
drafting German content in the AMAwalls brand voice. Modular so later phases
(Products, …) drop in as new module files without touching core.

## Confirmed configuration

| Setting | Value |
|---|---|
| Runtime | Python 3.11+ |
| Development | Built locally (this repo), deployed to `arturserver` over SSH |
| Process manager | systemd unit |
| Transport | `TRANSPORT=polling` (default) or `webhook` — server is public, both wired |
| Questioning depth | Fixed 3 questions before drafting an article |
| Shopify API | Admin GraphQL only, version `2026-07` |
| Content language | German (`de`) |

## Golden rules (from the brief — non-negotiable)

1. No hardcoded secrets; everything sensitive in gitignored `.env`; `.env.example` ships with every key documented and empty.
2. Shopify Admin **GraphQL** API only (`/admin/api/{version}/graphql.json`). No REST.
3. Draft-first: articles created with `isPublished: false`; going live requires an explicit button tap.
4. Dev store first; production is only a `.env` swap.
5. Two-layer auth: Telegram user-ID allowlist (silent drop for others) + password unlock.
6. Audit every action to the database.

## Stack

- `python-telegram-bot` v21+ — async application, long-polling and webhook support.
- `anthropic` — content generation (Messages API).
- `httpx` — Shopify Admin GraphQL calls.
- `sqlite3` (stdlib) — single-file persistent state.
- `python-dotenv` — `.env` loading.

## Repository layout

```
amma_bot/
├── bot/
│   ├── main.py            # entrypoint: config → DB → module registry → run polling/webhook
│   ├── config.py          # .env loading + fail-fast validation of required keys
│   ├── auth.py            # allowlist gate, password unlock, session helpers
│   ├── db.py              # SQLite schema + access: sessions, drafts, audit_log
│   ├── audit.py           # audit-log helper
│   ├── claude_client.py   # brand-voice system prompt; draft / self-check / alt-text
│   ├── shopify_client.py  # GraphQL client: articleCreate/Update/Delete, list articles
│   └── modules/
│       ├── registry.py    # module protocol + dispatcher wiring
│       └── blog.py        # Phase 1 module
├── scripts/
│   └── smoke_test.py      # verifies Shopify creds + BLOG_ID: create then delete a draft
├── deploy/
│   ├── amma-bot.service   # systemd unit
│   └── deploy.sh          # rsync to arturserver + restart service
├── .env.example
├── .gitignore             # ignores .env and *.db
├── requirements.txt
└── README.md              # setup, run, deploy, how to add a module
```

## Core architecture

### Module protocol (`modules/registry.py`)

Each module is a file exposing:

- `NAME` (e.g. `"blog"`) — doubles as the `callback_data` namespace prefix.
- `menu_button()` — the InlineKeyboardButton for the main menu.
- `register(app, services)` — adds its `CallbackQueryHandler`s (pattern `^blog:`)
  and declares its conversation steps.
- `handle_step(step, update, context)` — called by the central text-message router
  when the user's session step belongs to this module.

`main.py` iterates a `MODULES` list; adding Products later = one new file + one
list entry. The main menu also shows a disabled "Product (coming soon)" button.

### Conversation state

Persisted in the `sessions` table (`step` column + `context_json`), not in
PTB's in-memory ConversationHandler. A single text-message router reads the
user's current step and delegates to the owning module. Survives restarts.

Rejected alternative: PTB ConversationHandler + pickle persistence — more
idiomatic but couples module wiring into core and persists opaque blobs.

### Auth (`auth.py`)

- Layer 1 — allowlist: every update from a non-allowlisted user ID is dropped
  with **no reply** (audit-logged as `denied`). Implemented as a check at the
  top of every handler entry point (decorator).
- Layer 2 — password: allowlisted but locked users are prompted for the
  password on any interaction. Constant-time comparison (`hmac.compare_digest`)
  against `BOT_PASSWORD`. Unlock persists in `sessions` (no TTL; single
  operator — TTL is an easy later add).

### Database (`db.py`) — SQLite, single file

- `sessions(user_id PK, unlocked, step, context_json, updated_at)`
- `drafts(id PK short, user_id, shopify_article_gid, title_a, title_b,
  chosen_title, body_html, summary, tags, status, created_at)` — `id` is the
  short token embedded in `callback_data` (`blog:pub:<id>`); Telegram only
  echoes that string back, the bot looks up the rest.
- `audit_log(id, user_id, action, target, result, detail, created_at)`

### Claude client (`claude_client.py`)

System prompt encodes the AMAwalls brand voice (German; warm / organic /
accessible-premium; problem-first hook about awkward wall spaces — narrow gaps,
slanted ceilings, alcoves, recesses, between-window spaces, above-bed walls;
products: custom-sized textile prints with interchangeable frames + acoustic
panels; featured designs: Silent Jelly, Unberührt, Poppy Seed Explosion;
informative blog post, not an ad, soft CTA at most).

Functions (Messages API, structured JSON output):
- `draft_article(answers) -> {title_a, title_b, body_html, summary, tags}`
- `self_check(draft) -> {ok, issues}` — run after drafting; issues surfaced in preview
- `alt_text(image_desc) -> str`

Errors: one retry on transient failure, then report to chat.

### Shopify client (`shopify_client.py`)

`POST https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json`
with `X-Shopify-Access-Token`. Operations:

- `articleCreate` — always `isPublished: false`
- `articleUpdate` — field edits and the publish flip (`isPublished: true`)
- `articleDelete`
- `list_articles(blog_gid, n)` — recent articles for edit/delete pickers

Every mutation response checks `userErrors` and raises with the messages;
handlers report them verbatim into the chat.

## Phase 1 user flow

1. `/start` → not allowlisted: silence. Allowlisted: password prompt →
   correct: unlocked, main menu **[Blog] [Product (coming soon)]**.
2. Blog menu: **New article · Edit existing · Delete existing · Back**.
3. **New article** — three fixed questions, one message each:
   1. Awkward-wall angle / topic?
   2. Which featured design to spotlight? (buttons: Silent Jelly / Unberührt /
      Poppy Seed Explosion / none)
   3. Anything that must be included? (or `-` to skip)
   → Claude drafts → self-check → `articleCreate` (draft) → preview message
   (chosen title, summary, admin link, any self-check issues) with buttons:
   **Publish · Regenerate · Title A/B · Edit · Discard**.
   - Publish → `articleUpdate isPublished:true` → edit message to show live link.
   - Title A/B → toggle `chosen_title`, `articleUpdate` the title, refresh preview.
   - Regenerate → `articleDelete` old, re-draft from same answers, new draft.
   - Edit → free-text instruction → Claude revises → `articleUpdate`.
   - Discard → `articleDelete`, edit message to confirm.
4. **Edit existing** — recent articles as buttons → pick → choose field
   (title / body via Claude re-instruction) → `articleUpdate`.
5. **Delete existing** — recent articles → pick → explicit confirm button →
   `articleDelete`.

Every step writes `audit_log`.

## Error handling

- Config: fail fast at startup listing missing `.env` keys.
- Shopify `userErrors` / HTTP errors: reported to chat verbatim; audited as failures.
- Claude API: one retry, then chat report.
- Callback for an unknown/expired draft id: friendly "draft no longer exists" + menu.

## Testing

- `scripts/smoke_test.py` — standalone; creates a throwaway draft in the dev
  store, verifies it, deletes it. Run before first bot start.
- Unit tests for auth gating, db round-trips, callback-data parsing, and
  Shopify client userErrors handling (HTTP mocked).
- Manual E2E against dev store: create → approve → publish demonstrated first,
  then edit/delete.

## Deployment

- `deploy/amma-bot.service` — systemd unit, `Restart=always`, `EnvironmentFile`
  pointing at the server-side `.env`.
- `deploy/deploy.sh` — rsync the repo (minus `.env`, db, venv) to arturserver,
  install deps, restart the unit.
- Going live = swapping `SHOPIFY_STORE_DOMAIN` + `SHOPIFY_ADMIN_TOKEN` in `.env`.

## Out of scope for Phase 1

Products module, image generation/upload, webhook-mode hardening beyond the
config flag, multi-language content, session TTL.
