# AMAwalls Ops Bot

A Telegram bot for the AMAwalls team to run store operations from chat. It's built with
`python-telegram-bot`, stores its state in SQLite, drafts and publishes blog articles through
the Shopify Admin GraphQL API, and generates on-brand copy with Claude. Access is gated by a
two-layer check: a Telegram user-ID allowlist, then a shared password to unlock the menu. The
first shipped module is Blog (generate a draft, review it, publish or discard it); more modules
can be added without touching the core.

## Setup

```bash
git clone <repo-url> amma_bot
cd amma_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Then fill in every key in `.env`. `.env.example` documents each one with a comment; the table
below explains where each value comes from.

| Key | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) (revoke old tokens after rotating). |
| `BOT_PASSWORD` | Shared unlock password — the second auth layer on top of the allowlist. |
| `ALLOWLIST_USER_IDS` | Comma-separated Telegram numeric user IDs allowed to use the bot. Get your numeric ID from [@userinfobot](https://t.me/userinfobot). |
| `ANTHROPIC_API_KEY` | Anthropic API key used for content generation. |
| `CLAUDE_MODEL` | Optional model override (default `claude-sonnet-5`). |
| `SHOPIFY_STORE_DOMAIN` | Store domain to operate on, e.g. `your-dev-store.myshopify.com`. Use the dev store until you're ready to go live. |
| `SHOPIFY_ADMIN_TOKEN` | Admin API access token from a Shopify custom app. The app needs the `read_content` and `write_content` scopes. |
| `SHOPIFY_API_VERSION` | Shopify Admin API version, e.g. `2026-07`. |
| `BLOG_ID` | GID of the blog to post into, e.g. `gid://shopify/Blog/123456789`. Find it by running `{ blogs(first: 5) { nodes { id title } } }` in the Shopify GraphiQL app (Settings → Apps → your custom app → GraphiQL, or via the Shopify admin's GraphiQL explorer). |
| `AUTHOR_NAME` | Byline used for created articles. |
| `CONTENT_LANG` | Language for generated content (default `de`). |
| `TRANSPORT` | `polling` (default, no public URL needed) or `webhook`. |
| `WEBHOOK_URL` | Only needed when `TRANSPORT=webhook`. Public base URL Telegram will call. |
| `WEBHOOK_PORT` | Only needed when `TRANSPORT=webhook`. Local port to listen on (default `8443`). |
| `DB_PATH` | Path to the SQLite database file (default `amma_bot.db`). |

**Getting each secret:**
- **Telegram bot token**: talk to [@BotFather](https://t.me/BotFather), `/newbot` (or reuse an
  existing bot), copy the token.
- **Telegram user ID**: message [@userinfobot](https://t.me/userinfobot); it replies with your
  numeric ID. Add it to `ALLOWLIST_USER_IDS`.
- **Shopify Admin token**: in the Shopify admin, go to Settings → Apps and sales channels →
  Develop apps → create a custom app. Under Configuration, grant the Admin API scopes
  `read_content` and `write_content`. Install the app, then reveal the Admin API access token.
- **Blog ID**: open the custom app's GraphiQL explorer (or the Shopify admin GraphiQL app) and
  run:
  ```graphql
  { blogs(first: 5) { nodes { id title } } }
  ```
  Copy the `id` (a `gid://shopify/Blog/...` string) for the blog you want to post into.

## Verify

Run the test suite:

```bash
python -m pytest
```

Then, with a filled `.env`, run the smoke test — it creates a throwaway draft article on the
configured Shopify blog and deletes it, confirming credentials and `BLOG_ID` are correct:

```bash
python scripts/smoke_test.py
```

It must print `SMOKE TEST PASSED` before you start the bot for the first time.

## Run

From the repo root, with `.env` filled in:

```bash
python -m bot.main
```

By default this uses long polling (`TRANSPORT=polling`), which needs no public URL. To run in
webhook mode instead, set `TRANSPORT=webhook` and provide `WEBHOOK_URL` (the public base URL
Telegram will call) and optionally `WEBHOOK_PORT` (default `8443`) in `.env`.

## Deploy

Deployment targets a host reachable over SSH (default alias `arturserver`) and runs the bot
under systemd as user `amma` from `/opt/amma_bot`.

**One-time server setup:**

```bash
# on the server
sudo useradd --system --create-home --shell /usr/sbin/nologin amma
sudo mkdir -p /opt/amma_bot
sudo chown amma:amma /opt/amma_bot
```

Then copy `.env.example` to `/opt/amma_bot/.env` and fill it in on the server (this file is
never synced by the deploy script, and it swaps in production Shopify credentials — see "Going
live" below).

**Deploy / redeploy:**

```bash
./deploy/deploy.sh arturserver
```

This rsyncs the repo to `/opt/amma_bot` on the target host (excluding `.env`, `*.db`, `.venv`,
`.git`, `__pycache__`, `.pytest_cache`), creates/updates the virtualenv, installs
`requirements.txt`, installs `deploy/amma-bot.service` as the systemd unit, and restarts the
`amma-bot` service.

`deploy.sh` requires passwordless sudo on the target host for its `systemctl`/`cp` steps (no TTY
is available for a sudo password prompt over the non-interactive SSH session it uses).

The systemd unit (`deploy/amma-bot.service`) runs `/opt/amma_bot/.venv/bin/python -m bot.main`
as the `amma` user with `Restart=always`. `.env` is loaded from the working directory by
`python-dotenv`, so no `EnvironmentFile=` is needed in the unit.

## How to add a new module

1. Create `bot/modules/<name>.py` exposing:
   - `NAME` — the module's short identifier (used to namespace callbacks/steps).
   - `MENU_LABEL` — the button label shown in the main menu.
   - `register(app)` — registers the module's PTB handlers on the `Application`.
   - `handle_step(step, update, context)` — handles the next text message when a user session
     is mid-flow in this module.
2. Namespace every `callback_data` and every `step` value as `"<name>:..."` (see
   `bot/modules/blog.py` for the pattern) so the router in `bot/main.py` can dispatch correctly.
3. Add the module to the `modules=[...]` list in `bot/main.py:main()`.

Nothing else changes — the menu, auth gate, and router pick up the new module automatically.

## Going live

To switch the bot from the dev store to the live store: in `.env`, update
`SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ADMIN_TOKEN`, and `BLOG_ID` (if the live store's blog GID
differs), then restart the service:

```bash
sudo systemctl restart amma-bot
```
