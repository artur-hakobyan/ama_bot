# AMAwalls Ops Bot — Phase 1 (Blog) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A modular, long-running Telegram bot that lets allowlisted users create, publish, edit, and delete AMAwalls Shopify blog articles, with Claude drafting German content in the brand voice.

**Architecture:** python-telegram-bot v22 application with a module registry (`bot/modules/`); conversation state, drafts, and audit log persisted in a single SQLite file; Shopify Admin **GraphQL** client and Anthropic client injected via a `Services` container in `bot_data`. Draft-first: articles are created `isPublished: false` and only published on button tap.

**Tech Stack:** Python 3.11+, python-telegram-bot[webhooks]~=22.0, anthropic, httpx, sqlite3 (stdlib), python-dotenv, pytest + pytest-asyncio.

## Global Constraints

- No hardcoded secrets — everything from `.env` (gitignored); `.env.example` documents every key, values empty.
- Shopify Admin **GraphQL only**: `POST https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json` with header `X-Shopify-Access-Token`. Never REST. Always surface `userErrors`.
- Articles are created `isPublished: false`; publishing requires explicit button tap (`articleUpdate` → `isPublished: true`).
- Non-allowlisted users get **no reply at all** (but are audit-logged).
- Every state-changing action writes to `audit_log`.
- `SHOPIFY_API_VERSION=2026-07`, `CONTENT_LANG=de`, transport default `polling` (webhook switchable via `TRANSPORT`).
- Git remote: `git@github.com:artur-hakobyan/ama_bot.git` — push after each task.
- Run tests with `python -m pytest` from the repo root.

---

### Task 1: Scaffold + config loader

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `.env.example`, `bot/__init__.py`, `bot/config.py`
- Test: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `Config` frozen dataclass with attrs `telegram_bot_token, bot_password, allowlist_user_ids (frozenset[int]), anthropic_api_key, shopify_store_domain, shopify_admin_token, shopify_api_version, blog_id, content_lang, transport, webhook_url, webhook_port (int), claude_model, db_path, author_name`; classmethod `Config.load(env: Mapping = os.environ) -> Config`; raises `ConfigError` listing all missing keys.

- [ ] **Step 1: Create venv and scaffold files**

```bash
cd /Users/arturhakobyan/www/amma_bot
python3 -m venv .venv && source .venv/bin/activate
```

`requirements.txt`:
```
python-telegram-bot[webhooks]~=22.0
anthropic>=0.40
httpx>=0.27
python-dotenv>=1.0
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.24
```

`pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

`.env.example`:
```bash
# --- Telegram ---
# Bot token from @BotFather (revoke old tokens after rotating)
TELEGRAM_BOT_TOKEN=
# Shared unlock password (second auth layer on top of the allowlist)
BOT_PASSWORD=
# Comma-separated Telegram numeric user IDs allowed to use the bot
ALLOWLIST_USER_IDS=

# --- Anthropic (content generation) ---
ANTHROPIC_API_KEY=
# Optional: model override (default claude-sonnet-5)
CLAUDE_MODEL=

# --- Shopify (use the DEV store; going live = swap domain+token) ---
SHOPIFY_STORE_DOMAIN=your-dev-store.myshopify.com
# Admin API access token from the custom app (needs read/write content scopes)
SHOPIFY_ADMIN_TOKEN=
SHOPIFY_API_VERSION=2026-07
# Which blog to post into, e.g. gid://shopify/Blog/123456789
BLOG_ID=
# Byline for created articles
AUTHOR_NAME=AMAwalls Team

# --- Content ---
CONTENT_LANG=de

# --- Transport: polling (default, no public URL needed) or webhook ---
TRANSPORT=polling
# Only needed when TRANSPORT=webhook
WEBHOOK_URL=
WEBHOOK_PORT=8443

# --- Storage ---
DB_PATH=amma_bot.db
```

```bash
pip install -r requirements-dev.txt
touch bot/__init__.py tests/__init__.py  # after mkdir -p bot tests
```

- [ ] **Step 2: Write the failing tests** — `tests/test_config.py`:

```python
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
    assert "BOT_PASSWORD" in str(exc.value) and "BLOG_ID" in str(exc.value)

def test_bad_allowlist():
    with pytest.raises(ConfigError):
        Config.load({**VALID_ENV, "ALLOWLIST_USER_IDS": "abc"})

def test_webhook_requires_url():
    with pytest.raises(ConfigError):
        Config.load({**VALID_ENV, "TRANSPORT": "webhook"})
```

- [ ] **Step 3: Run to verify failure** — `python -m pytest tests/test_config.py -v` → FAIL (`ModuleNotFoundError: bot.config`).

- [ ] **Step 4: Implement** — `bot/config.py`:

```python
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
```

- [ ] **Step 5: Run tests** — `python -m pytest tests/test_config.py -v` → 4 PASS.

- [ ] **Step 6: Commit + push**

```bash
git add requirements.txt requirements-dev.txt pytest.ini .env.example bot tests
git commit -m "feat: scaffold project with fail-fast config loader"
git push
```

---

### Task 2: Database layer (sessions, drafts, audit_log)

**Files:**
- Create: `bot/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `Database(path: str)` with methods:
  - `get_session(user_id: int) -> dict` — keys `user_id, unlocked (bool), step (str|None), context (dict)`; auto-creates the row.
  - `set_unlocked(user_id: int, unlocked: bool) -> None`
  - `set_step(user_id: int, step: str | None, context: dict | None = None) -> None` — `context=None` keeps existing context; `step=None` clears the step.
  - `create_draft(user_id, title_a, title_b, body_html, summary, tags: list[str]) -> str` (8-char hex id)
  - `get_draft(draft_id: str) -> dict | None` — keys `id, user_id, shopify_article_gid, title_a, title_b, chosen_title ('a'|'b'), body_html, summary, tags (list), status`
  - `update_draft(draft_id: str, **fields) -> None` — whitelisted columns: `shopify_article_gid, chosen_title, status, title_a, title_b, body_html, summary`
  - `delete_draft(draft_id: str) -> None`
  - `log_audit(user_id: int, action: str, target: str, result: str, detail: str = "") -> None`
  - `close() -> None`

- [ ] **Step 1: Write the failing tests** — `tests/test_db.py`:

```python
import pytest
from bot.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()

def test_session_autocreate_and_unlock(db):
    s = db.get_session(42)
    assert s["unlocked"] is False and s["step"] is None and s["context"] == {}
    db.set_unlocked(42, True)
    assert db.get_session(42)["unlocked"] is True

def test_step_and_context(db):
    db.set_step(7, "blog:topic", {"x": 1})
    s = db.get_session(7)
    assert s["step"] == "blog:topic" and s["context"] == {"x": 1}
    db.set_step(7, "blog:design")          # context preserved
    assert db.get_session(7)["context"] == {"x": 1}
    db.set_step(7, None)
    assert db.get_session(7)["step"] is None

def test_draft_roundtrip(db):
    did = db.create_draft(42, "TA", "TB", "<p>hi</p>", "sum", ["a", "b"])
    d = db.get_draft(did)
    assert d["title_a"] == "TA" and d["tags"] == ["a", "b"] and d["chosen_title"] == "a"
    db.update_draft(did, shopify_article_gid="gid://shopify/Article/1", chosen_title="b")
    assert db.get_draft(did)["chosen_title"] == "b"
    db.delete_draft(did)
    assert db.get_draft(did) is None

def test_update_draft_rejects_unknown_column(db):
    did = db.create_draft(1, "a", "b", "c", "d", [])
    with pytest.raises(ValueError):
        db.update_draft(did, evil="x; DROP TABLE drafts")

def test_audit(db):
    db.log_audit(42, "publish", "gid://shopify/Article/1", "ok", "live")
    rows = db._conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1 and rows[0]["action"] == "publish"
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_db.py -v` → FAIL.

- [ ] **Step 3: Implement** — `bot/db.py`:

```python
import json
import secrets
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  user_id INTEGER PRIMARY KEY,
  unlocked INTEGER NOT NULL DEFAULT 0,
  step TEXT,
  context_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  shopify_article_gid TEXT,
  title_a TEXT,
  title_b TEXT,
  chosen_title TEXT NOT NULL DEFAULT 'a',
  body_html TEXT,
  summary TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  result TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DRAFT_COLUMNS = {
    "shopify_article_gid", "chosen_title", "status",
    "title_a", "title_b", "body_html", "summary",
}


class Database:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # --- sessions ---
    def get_session(self, user_id: int) -> dict:
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions (user_id) VALUES (?)", (user_id,))
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
        return {
            "user_id": row["user_id"],
            "unlocked": bool(row["unlocked"]),
            "step": row["step"],
            "context": json.loads(row["context_json"]),
        }

    def set_unlocked(self, user_id: int, unlocked: bool):
        self.get_session(user_id)
        self._conn.execute(
            "UPDATE sessions SET unlocked = ?, updated_at = datetime('now') WHERE user_id = ?",
            (int(unlocked), user_id))
        self._conn.commit()

    def set_step(self, user_id: int, step, context: dict | None = None):
        self.get_session(user_id)
        if context is None:
            self._conn.execute(
                "UPDATE sessions SET step = ?, updated_at = datetime('now') WHERE user_id = ?",
                (step, user_id))
        else:
            self._conn.execute(
                "UPDATE sessions SET step = ?, context_json = ?, updated_at = datetime('now') WHERE user_id = ?",
                (step, json.dumps(context), user_id))
        self._conn.commit()

    # --- drafts ---
    def create_draft(self, user_id, title_a, title_b, body_html, summary, tags) -> str:
        draft_id = secrets.token_hex(4)
        self._conn.execute(
            "INSERT INTO drafts (id, user_id, title_a, title_b, body_html, summary, tags_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (draft_id, user_id, title_a, title_b, body_html, summary, json.dumps(tags)))
        self._conn.commit()
        return draft_id

    def get_draft(self, draft_id: str):
        row = self._conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["tags"] = json.loads(d.pop("tags_json"))
        return d

    def update_draft(self, draft_id: str, **fields):
        unknown = set(fields) - DRAFT_COLUMNS
        if unknown:
            raise ValueError(f"Unknown draft columns: {unknown}")
        sets = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(
            f"UPDATE drafts SET {sets} WHERE id = ?",
            (*fields.values(), draft_id))
        self._conn.commit()

    def delete_draft(self, draft_id: str):
        self._conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        self._conn.commit()

    # --- audit ---
    def log_audit(self, user_id, action, target, result, detail=""):
        self._conn.execute(
            "INSERT INTO audit_log (user_id, action, target, result, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, action, target, result, detail))
        self._conn.commit()
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_db.py -v` → 5 PASS.

- [ ] **Step 5: Commit + push**

```bash
git add bot/db.py tests/test_db.py
git commit -m "feat: SQLite persistence for sessions, drafts, audit log"
git push
```

---

### Task 3: Shopify GraphQL client

**Files:**
- Create: `bot/shopify_client.py`
- Test: `tests/test_shopify_client.py`

**Interfaces:**
- Consumes: nothing internal (pure client; caller passes config values).
- Produces: `ShopifyError(Exception)`; `ShopifyClient(domain, token, version, client: httpx.AsyncClient | None = None)` with async methods:
  - `create_article(blog_id, title, body_html, summary, tags: list[str], author_name) -> dict` — article dict `{id, title, handle, isPublished}`, always sends `isPublished: False`
  - `update_article(article_gid, fields: dict) -> dict` — same article dict
  - `publish_article(article_gid) -> dict` — sugar for `update_article(gid, {"isPublished": True})`
  - `delete_article(article_gid) -> str` — deleted gid
  - `get_article(article_gid) -> dict` — `{id, title, handle, body, isPublished}`
  - `list_articles(blog_id, first=10) -> tuple[str, list[dict]]` — `(blog_handle, articles)`
  - `admin_url(article_gid) -> str` and `live_url(blog_handle, article_handle) -> str` (sync helpers)
  - All mutations raise `ShopifyError` on HTTP != 200, GraphQL `errors`, or non-empty `userErrors` (messages joined into the exception text).

- [ ] **Step 1: Write the failing tests** — `tests/test_shopify_client.py`:

```python
import json
import httpx
import pytest
from bot.shopify_client import ShopifyClient, ShopifyError

def make_client(responder):
    transport = httpx.MockTransport(responder)
    http = httpx.AsyncClient(transport=transport)
    return ShopifyClient("dev.myshopify.com", "tok", "2026-07", client=http)

def gql_response(payload, status=200):
    return httpx.Response(status, json=payload)

async def test_create_article_sends_draft_and_token():
    captured = {}
    def responder(request):
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return gql_response({"data": {"articleCreate": {
            "article": {"id": "gid://shopify/Article/1", "title": "T",
                        "handle": "t", "isPublished": False},
            "userErrors": []}}})
    c = make_client(responder)
    art = await c.create_article("gid://shopify/Blog/9", "T", "<p>b</p>", "s", ["x"], "Author")
    assert art["id"] == "gid://shopify/Article/1"
    assert captured["headers"]["x-shopify-access-token"] == "tok"
    assert captured["body"]["variables"]["article"]["isPublished"] is False
    assert captured["url"].endswith("/admin/api/2026-07/graphql.json")

async def test_user_errors_raise():
    def responder(request):
        return gql_response({"data": {"articleCreate": {
            "article": None,
            "userErrors": [{"field": ["title"], "message": "can't be blank"}]}}})
    c = make_client(responder)
    with pytest.raises(ShopifyError, match="can't be blank"):
        await c.create_article("gid://shopify/Blog/9", "", "b", "s", [], "A")

async def test_http_error_raises():
    c = make_client(lambda r: httpx.Response(401, text="denied"))
    with pytest.raises(ShopifyError, match="401"):
        await c.get_article("gid://shopify/Article/1")

async def test_list_articles():
    def responder(request):
        return gql_response({"data": {"blog": {"handle": "news", "articles": {"nodes": [
            {"id": "gid://shopify/Article/1", "title": "A", "handle": "a",
             "isPublished": True, "publishedAt": "2026-01-01"}]}}}})
    c = make_client(responder)
    handle, arts = await c.list_articles("gid://shopify/Blog/9")
    assert handle == "news" and arts[0]["title"] == "A"

def test_urls():
    c = ShopifyClient("dev.myshopify.com", "tok", "2026-07",
                      client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert c.admin_url("gid://shopify/Article/123") == "https://dev.myshopify.com/admin/articles/123"
    assert c.live_url("news", "my-post") == "https://dev.myshopify.com/blogs/news/my-post"
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_shopify_client.py -v` → FAIL.

- [ ] **Step 3: Implement** — `bot/shopify_client.py`:

```python
import httpx


class ShopifyError(Exception):
    pass


ARTICLE_FIELDS = "id title handle isPublished"

CREATE_ARTICLE = f"""
mutation CreateArticle($article: ArticleCreateInput!) {{
  articleCreate(article: $article) {{
    article {{ {ARTICLE_FIELDS} }}
    userErrors {{ field message }}
  }}
}}"""

UPDATE_ARTICLE = f"""
mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {{
  articleUpdate(id: $id, article: $article) {{
    article {{ {ARTICLE_FIELDS} }}
    userErrors {{ field message }}
  }}
}}"""

DELETE_ARTICLE = """
mutation DeleteArticle($id: ID!) {
  articleDelete(id: $id) {
    deletedArticleId
    userErrors { field message }
  }
}"""

GET_ARTICLE = f"""
query GetArticle($id: ID!) {{
  node(id: $id) {{
    ... on Article {{ {ARTICLE_FIELDS} body }}
  }}
}}"""

LIST_ARTICLES = f"""
query ListArticles($blogId: ID!, $first: Int!) {{
  blog(id: $blogId) {{
    handle
    articles(first: $first, sortKey: UPDATED_AT, reverse: true) {{
      nodes {{ {ARTICLE_FIELDS} publishedAt }}
    }}
  }}
}}"""


class ShopifyClient:
    def __init__(self, domain: str, token: str, version: str,
                 client: httpx.AsyncClient | None = None):
        self._domain = domain
        self._url = f"https://{domain}/admin/api/{version}/graphql.json"
        self._headers = {"X-Shopify-Access-Token": token,
                         "Content-Type": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _execute(self, query: str, variables: dict) -> dict:
        resp = await self._client.post(
            self._url, json={"query": query, "variables": variables},
            headers=self._headers)
        if resp.status_code != 200:
            raise ShopifyError(f"Shopify HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("errors"):
            raise ShopifyError(f"Shopify GraphQL errors: {data['errors']}")
        return data["data"]

    @staticmethod
    def _check_user_errors(payload: dict, op: str):
        errors = payload.get("userErrors") or []
        if errors:
            msgs = "; ".join(
                f"{'/'.join(e.get('field') or ['?'])}: {e['message']}" for e in errors)
            raise ShopifyError(f"{op} failed: {msgs}")

    async def create_article(self, blog_id, title, body_html, summary, tags, author_name) -> dict:
        article = {
            "blogId": blog_id, "title": title, "body": body_html,
            "summary": summary, "tags": tags, "isPublished": False,
            "author": {"name": author_name},
        }
        data = await self._execute(CREATE_ARTICLE, {"article": article})
        payload = data["articleCreate"]
        self._check_user_errors(payload, "articleCreate")
        return payload["article"]

    async def update_article(self, article_gid: str, fields: dict) -> dict:
        data = await self._execute(UPDATE_ARTICLE, {"id": article_gid, "article": fields})
        payload = data["articleUpdate"]
        self._check_user_errors(payload, "articleUpdate")
        return payload["article"]

    async def publish_article(self, article_gid: str) -> dict:
        return await self.update_article(article_gid, {"isPublished": True})

    async def delete_article(self, article_gid: str) -> str:
        data = await self._execute(DELETE_ARTICLE, {"id": article_gid})
        payload = data["articleDelete"]
        self._check_user_errors(payload, "articleDelete")
        return payload["deletedArticleId"]

    async def get_article(self, article_gid: str) -> dict:
        data = await self._execute(GET_ARTICLE, {"id": article_gid})
        node = data.get("node")
        if not node:
            raise ShopifyError(f"Article not found: {article_gid}")
        return node

    async def list_articles(self, blog_id: str, first: int = 10):
        data = await self._execute(LIST_ARTICLES, {"blogId": blog_id, "first": first})
        blog = data.get("blog")
        if not blog:
            raise ShopifyError(f"Blog not found: {blog_id}")
        return blog["handle"], blog["articles"]["nodes"]

    def admin_url(self, article_gid: str) -> str:
        num = article_gid.rsplit("/", 1)[-1]
        return f"https://{self._domain}/admin/articles/{num}"

    def live_url(self, blog_handle: str, article_handle: str) -> str:
        return f"https://{self._domain}/blogs/{blog_handle}/{article_handle}"
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_shopify_client.py -v` → 6 PASS.

- [ ] **Step 5: Commit + push**

```bash
git add bot/shopify_client.py tests/test_shopify_client.py
git commit -m "feat: Shopify Admin GraphQL client with userErrors surfacing"
git push
```

---

### Task 4: Smoke test script

**Files:**
- Create: `scripts/smoke_test.py`

**Interfaces:**
- Consumes: `Config.load()` (Task 1), `ShopifyClient` (Task 3).
- Produces: standalone script; exit 0 on success, exit 1 with the error printed on failure.

- [ ] **Step 1: Implement** — `scripts/smoke_test.py`:

```python
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
```

- [ ] **Step 2: Sanity-check it fails cleanly without credentials**

Run: `python scripts/smoke_test.py`
Expected (until `.env` is filled): `CONFIG ERROR: Missing required .env keys: …` and exit code 1 (`echo $?` → 1). With real dev-store credentials later: three `OK` lines + `SMOKE TEST PASSED`.

- [ ] **Step 3: Commit + push**

```bash
git add scripts/smoke_test.py
git commit -m "feat: Shopify credentials smoke test (create+delete throwaway draft)"
git push
```

---

### Task 5: Claude content client

**Files:**
- Create: `bot/claude_client.py`
- Test: `tests/test_claude_client.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces: `ClaudeError(Exception)`; `ClaudeClient(api_key, model, client=None)` (injectable `AsyncAnthropic`-compatible client) with async methods:
  - `draft_article(topic, design, must_include) -> dict` — keys `title_a, title_b, body_html, summary, tags (list[str])`
  - `revise_article(body_html, instruction) -> str` — revised HTML
  - `self_check(draft: dict) -> dict` — keys `ok (bool), issues (list[str])`
  - `alt_text(description: str) -> str`
  - One retry on any API exception, then `ClaudeError`. JSON responses parsed leniently (strips ``` fences); parse failure → `ClaudeError`.

- [ ] **Step 1: Write the failing tests** — `tests/test_claude_client.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.claude_client import ClaudeClient, ClaudeError

def fake_anthropic(text=None, side_effect=None):
    resp = SimpleNamespace(content=[SimpleNamespace(text=text or "")])
    create = AsyncMock(return_value=resp, side_effect=side_effect)
    return SimpleNamespace(messages=SimpleNamespace(create=create)), create

async def test_draft_article_parses_json():
    fake, _ = fake_anthropic('```json\n{"title_a":"A","title_b":"B","body_html":"<p>x</p>","summary":"s","tags":["wand"]}\n```')
    c = ClaudeClient("k", "m", client=fake)
    d = await c.draft_article("Dachschräge", "Silent Jelly", "-")
    assert d["title_a"] == "A" and d["tags"] == ["wand"]

async def test_draft_article_bad_json_raises():
    fake, _ = fake_anthropic("not json at all")
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError):
        await c.draft_article("t", "d", "-")

async def test_retry_once_then_raise():
    fake, create = fake_anthropic(side_effect=RuntimeError("boom"))
    c = ClaudeClient("k", "m", client=fake)
    with pytest.raises(ClaudeError, match="boom"):
        await c.alt_text("bild")
    assert create.await_count == 2

async def test_self_check():
    fake, _ = fake_anthropic('{"ok": false, "issues": ["zu werblich"]}')
    c = ClaudeClient("k", "m", client=fake)
    r = await c.self_check({"title_a": "A", "body_html": "<p></p>", "summary": ""})
    assert r["ok"] is False and r["issues"] == ["zu werblich"]
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_claude_client.py -v` → FAIL.

- [ ] **Step 3: Implement** — `bot/claude_client.py`:

```python
import json

from anthropic import AsyncAnthropic


class ClaudeError(Exception):
    pass


SYSTEM_PROMPT = """Du bist der Content-Autor von AMAwalls, einem Shop für maßgefertigte \
großformatige Textildrucke mit austauschbaren Rahmen sowie Akustikpaneele.

Sprache: Deutsch. Ton: warm, organisch, zugänglich-premium — niemals poliert, \
werblich oder aufdringlich.

Nische: ungewöhnliche, sperrige, unnormierte Wandflächen — schmale Nischen, \
Dachschrägen, Alkoven, Rücksprünge, Flächen zwischen Fenstern, Wände über dem Bett. \
Aufbau immer problem-first: Beginne mit der Herausforderung der schwierigen Wand, \
dann die maßgefertigte Lösung als Auflösung.

Featured Designs in Rotation: Silent Jelly, Unberührt, Poppy Seed Explosion.

Wichtig: Der Text ist ein Shopify-Blogartikel, KEINE Werbung — informativ und \
nützlich, höchstens ein sanfter Call-to-Action am Ende.

Wenn du nach JSON gefragt wirst, antworte NUR mit validem JSON, ohne Erklärtext."""


class ClaudeClient:
    def __init__(self, api_key: str, model: str, client=None):
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model

    async def _ask(self, prompt: str, max_tokens: int = 4096) -> str:
        last_error = None
        for _ in range(2):
            try:
                resp = await self._client.messages.create(
                    model=self._model, max_tokens=max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}])
                return resp.content[0].text
            except Exception as e:  # anthropic transport/API errors
                last_error = e
        raise ClaudeError(f"Claude request failed after retry: {last_error}")

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned.strip())
        except json.JSONDecodeError as e:
            raise ClaudeError(f"Claude returned invalid JSON: {e}")

    async def draft_article(self, topic: str, design: str, must_include: str) -> dict:
        prompt = f"""Schreibe einen Blogartikel.

Thema / schwierige Wandsituation: {topic}
Zu featurendes Design: {design}
Muss enthalten sein: {must_include}

Antworte als JSON mit exakt diesen Keys:
{{"title_a": "Titelvariante A (problem-first)",
 "title_b": "Titelvariante B (anderer Blickwinkel)",
 "body_html": "vollständiger Artikel als sauberes HTML (<p>, <h2>, <ul>), 600-900 Wörter",
 "summary": "2-3 Sätze Zusammenfassung",
 "tags": ["3-5", "deutsche", "tags"]}}"""
        draft = self._parse_json(await self._ask(prompt))
        missing = {"title_a", "title_b", "body_html", "summary", "tags"} - set(draft)
        if missing:
            raise ClaudeError(f"Draft missing keys: {missing}")
        return draft

    async def revise_article(self, body_html: str, instruction: str) -> str:
        prompt = f"""Überarbeite diesen Artikel nach der Anweisung. Antworte NUR mit dem \
vollständigen überarbeiteten HTML, ohne JSON, ohne Erklärung.

Anweisung: {instruction}

Artikel:
{body_html}"""
        return (await self._ask(prompt)).strip()

    async def self_check(self, draft: dict) -> dict:
        prompt = f"""Prüfe diesen Artikelentwurf gegen die Markenrichtlinien \
(deutsch, warm/organisch, problem-first, informativ statt werblich, sanfter CTA).

Titel: {draft.get("title_a")}
Zusammenfassung: {draft.get("summary")}
Artikel: {draft.get("body_html")}

Antworte als JSON: {{"ok": true/false, "issues": ["konkrete Probleme, leer wenn ok"]}}"""
        result = self._parse_json(await self._ask(prompt, max_tokens=1024))
        return {"ok": bool(result.get("ok")), "issues": list(result.get("issues") or [])}

    async def alt_text(self, description: str) -> str:
        prompt = (f"Schreibe einen prägnanten deutschen Alt-Text (max. 125 Zeichen) "
                  f"für dieses Bild: {description}. Antworte nur mit dem Alt-Text.")
        return (await self._ask(prompt, max_tokens=200)).strip()
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_claude_client.py -v` → 4 PASS.

- [ ] **Step 5: Commit + push**

```bash
git add bot/claude_client.py tests/test_claude_client.py
git commit -m "feat: Claude content client with AMAwalls brand-voice system prompt"
git push
```

---

### Task 6: Auth layer

**Files:**
- Create: `bot/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `Database` (Task 2).
- Produces:
  - `is_allowlisted(config, user_id: int) -> bool`
  - `check_password(config, attempt: str) -> bool` — constant-time
  - `authorized(handler)` — decorator for PTB handlers. Reads `services = context.bot_data["services"]` (a `Services` object from Task 7 with `.config` and `.db`). Non-allowlisted → audit `("access", "-", "denied")`, **no reply**, handler not called. Allowlisted+locked → reply `"🔒 Bitte Passwort eingeben."`, handler not called. Unlocked → handler runs.

- [ ] **Step 1: Write the failing tests** — `tests/test_auth.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.auth import authorized, check_password, is_allowlisted
from bot.config import Config

CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t", "BOT_PASSWORD": "geheim",
    "ALLOWLIST_USER_IDS": "111", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "g",
})

def test_allowlist_and_password():
    assert is_allowlisted(CFG, 111) and not is_allowlisted(CFG, 999)
    assert check_password(CFG, "geheim") and not check_password(CFG, "nope")

def make_update(user_id):
    msg = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           effective_message=msg)

def make_context(db, unlocked):
    services = SimpleNamespace(config=CFG, db=db)
    db.set_unlocked(111, unlocked)
    return SimpleNamespace(bot_data={"services": services})

@pytest.fixture
def db(tmp_path):
    from bot.db import Database
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()

async def test_denied_is_silent(db):
    inner = AsyncMock()
    update = make_update(999)
    await authorized(inner)(update, make_context(db, True))
    inner.assert_not_awaited()
    update.effective_message.reply_text.assert_not_awaited()

async def test_locked_prompts_password(db):
    inner = AsyncMock()
    update = make_update(111)
    await authorized(inner)(update, make_context(db, False))
    inner.assert_not_awaited()
    update.effective_message.reply_text.assert_awaited_once()

async def test_unlocked_runs_handler(db):
    inner = AsyncMock()
    update = make_update(111)
    await authorized(inner)(update, make_context(db, True))
    inner.assert_awaited_once()
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_auth.py -v` → FAIL.

- [ ] **Step 3: Implement** — `bot/auth.py`:

```python
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
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_auth.py -v` → 4 PASS.

- [ ] **Step 5: Commit + push**

```bash
git add bot/auth.py tests/test_auth.py
git commit -m "feat: two-layer auth (silent allowlist gate + password unlock)"
git push
```

---

### Task 7: Module registry, main menu, entrypoint

**Files:**
- Create: `bot/modules/__init__.py` (empty), `bot/modules/registry.py`, `bot/main.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `Services` dataclass in `registry.py`: fields `config, db, shopify, claude`.
  - Module protocol (duck-typed): a module is any object/module with `NAME: str`, `MENU_LABEL: str`, `register(app) -> None` (adds its CallbackQueryHandlers), `handle_step(step: str, update, context) -> Awaitable` (called when session step starts with `f"{NAME}:"`), and `show_menu(update, context) -> Awaitable`.
  - `main_menu_keyboard(modules) -> InlineKeyboardMarkup` — one row per module + disabled `Product (coming soon) 🚧` row with `callback_data="noop"`.
  - `bot/main.py`: `build_application(services, modules) -> Application` and `main()`. `main()` loads `.env`, builds `Services`, registers `/start`, the global text router, a `noop` callback, and each module; runs `run_polling()` or `run_webhook(listen="0.0.0.0", port=cfg.webhook_port, url_path=cfg.telegram_bot_token, webhook_url=f"{cfg.webhook_url}/{cfg.telegram_bot_token}")` per `cfg.transport`.
  - Text-router contract: non-allowlisted → silent; locked → treat message text as password attempt (correct → `set_unlocked`, show main menu; wrong → `"❌ Falsches Passwort."` and audit `("auth", "-", "failed")`); unlocked with session step `"<module>:…"` → that module's `handle_step`.
  - `/start` contract: non-allowlisted → silent (audited); locked → password prompt; unlocked → main menu `"AMAwalls Ops — was möchtest du tun?"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_registry.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.modules.registry import Services, main_menu_keyboard

def test_services_holds_dependencies():
    s = Services(config=1, db=2, shopify=3, claude=4)
    assert (s.config, s.db, s.shopify, s.claude) == (1, 2, 3, 4)

def test_main_menu_keyboard():
    mod = SimpleNamespace(NAME="blog", MENU_LABEL="📝 Blog")
    kb = main_menu_keyboard([mod])
    rows = kb.inline_keyboard
    assert rows[0][0].text == "📝 Blog" and rows[0][0].callback_data == "blog:menu"
    assert rows[-1][0].callback_data == "noop"
    assert "coming soon" in rows[-1][0].text.lower()
```

And the router behavior (add to same file):

```python
import pytest
from bot.config import Config
from bot.db import Database
from bot.main import start_cmd, text_router

CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t", "BOT_PASSWORD": "geheim",
    "ALLOWLIST_USER_IDS": "111", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "g",
})

@pytest.fixture
def services(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    yield Services(config=CFG, db=db, shopify=None, claude=None)
    db.close()

def make_update(user_id, text=""):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           effective_message=msg, message=msg)

def make_context(services, modules=()):
    return SimpleNamespace(bot_data={"services": services,
                                     "modules": list(modules)})

async def test_start_silent_for_stranger(services):
    u = make_update(999)
    await start_cmd(u, make_context(services))
    u.effective_message.reply_text.assert_not_awaited()

async def test_password_flow(services):
    ctx = make_context(services)
    u = make_update(111, "falsch")
    await text_router(u, ctx)
    assert "Falsches" in u.effective_message.reply_text.await_args.args[0]
    u2 = make_update(111, "geheim")
    await text_router(u2, ctx)
    assert services.db.get_session(111)["unlocked"] is True

async def test_router_dispatches_to_module_step(services):
    handle = AsyncMock()
    mod = SimpleNamespace(NAME="blog", MENU_LABEL="B", handle_step=handle)
    services.db.set_unlocked(111, True)
    services.db.set_step(111, "blog:topic")
    u = make_update(111, "Dachschräge")
    await text_router(u, make_context(services, [mod]))
    handle.assert_awaited_once()
    assert handle.await_args.args[0] == "blog:topic"
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_registry.py -v` → FAIL.

- [ ] **Step 3: Implement** — `bot/modules/registry.py`:

```python
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class Services:
    config: object
    db: object
    shopify: object
    claude: object


def main_menu_keyboard(modules) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(m.MENU_LABEL, callback_data=f"{m.NAME}:menu")]
            for m in modules]
    rows.append([InlineKeyboardButton("🚧 Product (coming soon)", callback_data="noop")])
    return InlineKeyboardMarkup(rows)
```

Then `bot/main.py`:

```python
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
    await query.edit_message_text(
        MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(context.bot_data["modules"]))


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
```

Note: `bot/modules/blog.py` doesn't exist yet — create a placeholder so imports work (Task 8 replaces it):

```python
"""Blog module — implemented in Task 8."""
NAME = "blog"
MENU_LABEL = "📝 Blog"

def register(app):
    pass

async def handle_step(step, update, context):
    pass
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_registry.py -v` → 6 PASS. Also `python -m pytest -v` (whole suite green).

- [ ] **Step 5: Commit + push**

```bash
git add bot/modules bot/main.py tests/test_registry.py
git commit -m "feat: module registry, auth-gated router, polling/webhook entrypoint"
git push
```

---

### Task 8: Blog module — New article (create → approve → publish loop)

**Files:**
- Create: `bot/modules/blog.py` (replace placeholder)
- Test: `tests/test_blog.py`

**Interfaces:**
- Consumes: `Services` via `context.bot_data["services"]`; `authorized` decorator; `db.create_draft/get_draft/update_draft/delete_draft`; `shopify.create_article/publish_article/delete_article/update_article/admin_url/live_url/list_articles`; `claude.draft_article/self_check/revise_article`.
- Produces: module implementing the registry protocol. `callback_data` grammar (≤64 bytes):
  - `blog:menu`, `blog:new`, `blog:design:<slug>` (slug ∈ `jelly|unberuehrt|poppy|none`)
  - draft actions: `blog:pub:<id>`, `blog:regen:<id>`, `blog:title:<id>`, `blog:editdraft:<id>`, `blog:discard:<id>`
  - Session steps owned: `blog:topic`, `blog:must`, `blog:editdraft` (context carries `draft_id`).
- Helper functions (module-level, unit-testable): `parse_cb(data) -> tuple[str, str|None]` (action, arg); `preview_text(draft, admin_url, issues) -> str`; `preview_keyboard(draft_id) -> InlineKeyboardMarkup`; `chosen_title(draft) -> str`.

- [ ] **Step 1: Write the failing tests** — `tests/test_blog.py`:

```python
from bot.modules import blog

def make_draft(**over):
    d = {"id": "abc123", "user_id": 1, "shopify_article_gid": "gid://shopify/Article/9",
         "title_a": "Titel A", "title_b": "Titel B", "chosen_title": "a",
         "body_html": "<p>x</p>", "summary": "Zusammenfassung", "tags": ["wand"],
         "status": "pending"}
    d.update(over)
    return d

def test_parse_cb():
    assert blog.parse_cb("blog:pub:abc123") == ("pub", "abc123")
    assert blog.parse_cb("blog:new") == ("new", None)

def test_chosen_title():
    assert blog.chosen_title(make_draft()) == "Titel A"
    assert blog.chosen_title(make_draft(chosen_title="b")) == "Titel B"

def test_preview_text_contains_essentials():
    text = blog.preview_text(make_draft(), "https://x/admin/articles/9", ["zu lang"])
    assert "Titel A" in text and "Zusammenfassung" in text
    assert "https://x/admin/articles/9" in text and "zu lang" in text

def test_preview_keyboard_actions():
    kb = blog.preview_keyboard("abc123")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == ["blog:pub:abc123", "blog:regen:abc123", "blog:title:abc123",
                     "blog:editdraft:abc123", "blog:discard:abc123"]

def test_design_slug_roundtrip():
    assert blog.DESIGNS["jelly"] == "Silent Jelly"
    assert blog.DESIGNS["unberuehrt"] == "Unberührt"
    assert blog.DESIGNS["poppy"] == "Poppy Seed Explosion"
    assert blog.DESIGNS["none"] == "kein bestimmtes Design"
```

Flow test with fakes (same file):

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.config import Config
from bot.db import Database
from bot.modules.registry import Services

CFG = Config.load({
    "TELEGRAM_BOT_TOKEN": "t", "BOT_PASSWORD": "pw",
    "ALLOWLIST_USER_IDS": "111", "ANTHROPIC_API_KEY": "k",
    "SHOPIFY_STORE_DOMAIN": "d", "SHOPIFY_ADMIN_TOKEN": "s",
    "SHOPIFY_API_VERSION": "2026-07", "BLOG_ID": "gid://shopify/Blog/1",
})

@pytest.fixture
def services(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.set_unlocked(111, True)
    shopify = SimpleNamespace(
        create_article=AsyncMock(return_value={"id": "gid://shopify/Article/9",
                                               "title": "Titel A", "handle": "titel-a",
                                               "isPublished": False}),
        publish_article=AsyncMock(return_value={"id": "gid://shopify/Article/9",
                                                "title": "Titel A", "handle": "titel-a",
                                                "isPublished": True}),
        delete_article=AsyncMock(return_value="gid://shopify/Article/9"),
        update_article=AsyncMock(),
        list_articles=AsyncMock(return_value=("news", [])),
        admin_url=lambda gid: f"https://d/admin/articles/{gid.rsplit('/', 1)[-1]}",
        live_url=lambda bh, ah: f"https://d/blogs/{bh}/{ah}",
    )
    claude = SimpleNamespace(
        draft_article=AsyncMock(return_value={
            "title_a": "Titel A", "title_b": "Titel B", "body_html": "<p>x</p>",
            "summary": "Zsf", "tags": ["wand"]}),
        self_check=AsyncMock(return_value={"ok": True, "issues": []}),
        revise_article=AsyncMock(return_value="<p>neu</p>"),
    )
    yield Services(config=CFG, db=db, shopify=shopify, claude=claude)
    db.close()

def make_ctx(services):
    return SimpleNamespace(bot_data={"services": services, "modules": []})

def make_text_update(text):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=111),
                           effective_message=msg, message=msg)

async def test_full_new_article_flow(services):
    ctx = make_ctx(services)
    # Q1: topic
    services.db.set_step(111, "blog:topic", {})
    await blog.handle_step("blog:topic", make_text_update("Dachschräge"), ctx)
    s = services.db.get_session(111)
    assert s["step"] is None  # waiting on design button, not a text step
    assert s["context"]["topic"] == "Dachschräge"
    # Q2 (design) is a callback; simulate its effect then Q3: must-include text
    services.db.set_step(111, "blog:must", {"topic": "Dachschräge", "design": "Silent Jelly"})
    u = make_text_update("-")
    await blog.handle_step("blog:must", u, ctx)
    # draft created in shopify as draft + db row exists
    services.shopify.create_article.assert_awaited_once()
    assert services.shopify.create_article.await_args.args[0] == CFG.blog_id
    # preview replied with keyboard
    reply = u.effective_message.reply_text
    assert reply.await_count >= 1
    kwargs = reply.await_args.kwargs
    assert kwargs.get("reply_markup") is not None
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_blog.py -v` → FAIL.

- [ ] **Step 3: Implement** — `bot/modules/blog.py` (full replacement):

```python
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.auth import authorized
from bot.claude_client import ClaudeError
from bot.shopify_client import ShopifyError

logger = logging.getLogger(__name__)

NAME = "blog"
MENU_LABEL = "📝 Blog"

DESIGNS = {
    "jelly": "Silent Jelly",
    "unberuehrt": "Unberührt",
    "poppy": "Poppy Seed Explosion",
    "none": "kein bestimmtes Design",
}


# --- pure helpers -----------------------------------------------------------

def parse_cb(data: str):
    parts = data.split(":", 2)          # "blog:action[:arg]"
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    return action, arg


def chosen_title(draft: dict) -> str:
    return draft["title_a"] if draft["chosen_title"] == "a" else draft["title_b"]


def preview_text(draft: dict, admin_url: str, issues: list) -> str:
    lines = [
        f"📄 *{chosen_title(draft)}*",
        "",
        draft["summary"] or "",
        "",
        f"Tags: {', '.join(draft['tags'])}" if draft["tags"] else "",
        f"Admin: {admin_url}",
        "",
        "Status: Entwurf (unveröffentlicht)",
    ]
    if issues:
        lines += ["", "⚠️ Selbst-Check:"] + [f"• {i}" for i in issues]
    return "\n".join(line for line in lines if line is not None)


def preview_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data=f"blog:pub:{draft_id}")],
        [InlineKeyboardButton("🔄 Regenerate", callback_data=f"blog:regen:{draft_id}")],
        [InlineKeyboardButton("🔀 Titel A/B", callback_data=f"blog:title:{draft_id}")],
        [InlineKeyboardButton("✏️ Edit", callback_data=f"blog:editdraft:{draft_id}")],
        [InlineKeyboardButton("🗑 Discard", callback_data=f"blog:discard:{draft_id}")],
    ])


def blog_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Neuer Artikel", callback_data="blog:new")],
        [InlineKeyboardButton("✏️ Bestehenden bearbeiten", callback_data="blog:listedit")],
        [InlineKeyboardButton("🗑 Bestehenden löschen", callback_data="blog:listdel")],
        [InlineKeyboardButton("⬅️ Zurück", callback_data="main:menu")],
    ])


def design_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"blog:design:{slug}")]
         for slug, label in DESIGNS.items()])


# --- draft creation flow ----------------------------------------------------

async def _create_and_preview(update, context, answers: dict, user_id: int):
    """Claude draft → self-check → Shopify draft article → preview message."""
    services = context.bot_data["services"]
    msg = update.effective_message
    await msg.reply_text("✍️ Claude schreibt den Entwurf …")
    try:
        draft_data = await services.claude.draft_article(
            answers["topic"], answers["design"], answers.get("must", "-"))
        check = await services.claude.self_check(draft_data)
    except ClaudeError as e:
        services.db.log_audit(user_id, "draft", "-", "error", str(e))
        await msg.reply_text(f"❌ Claude-Fehler: {e}")
        return
    draft_id = services.db.create_draft(
        user_id, draft_data["title_a"], draft_data["title_b"],
        draft_data["body_html"], draft_data["summary"], draft_data["tags"])
    try:
        article = await services.shopify.create_article(
            services.config.blog_id, draft_data["title_a"], draft_data["body_html"],
            draft_data["summary"], draft_data["tags"], services.config.author_name)
    except ShopifyError as e:
        services.db.delete_draft(draft_id)
        services.db.log_audit(user_id, "article_create", "-", "error", str(e))
        await msg.reply_text(f"❌ Shopify-Fehler: {e}")
        return
    services.db.update_draft(draft_id, shopify_article_gid=article["id"])
    services.db.log_audit(user_id, "article_create", article["id"], "ok",
                          f"draft {draft_id}")
    draft = services.db.get_draft(draft_id)
    await msg.reply_text(
        preview_text(draft, services.shopify.admin_url(article["id"]), check["issues"]),
        reply_markup=preview_keyboard(draft_id), parse_mode="Markdown",
        disable_web_page_preview=True)


# --- text-step handling (router calls this) ---------------------------------

async def handle_step(step: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    user_id = update.effective_user.id
    session = services.db.get_session(user_id)
    text = (update.effective_message.text or "").strip()
    ctx = session["context"]

    if step == "blog:topic":
        ctx["topic"] = text
        services.db.set_step(user_id, None, ctx)
        await update.effective_message.reply_text(
            "2/3 — Welches Design soll im Fokus stehen?",
            reply_markup=design_keyboard())
    elif step == "blog:must":
        ctx["must"] = text
        services.db.set_step(user_id, None, ctx)
        await _create_and_preview(update, context, ctx, user_id)
    elif step == "blog:editdraft":
        draft_id = ctx.get("draft_id")
        draft = services.db.get_draft(draft_id) if draft_id else None
        services.db.set_step(user_id, None, ctx)
        if draft is None:
            await update.effective_message.reply_text(
                "⚠️ Dieser Entwurf existiert nicht mehr.",
                reply_markup=blog_menu_keyboard())
            return
        await update.effective_message.reply_text("✍️ Claude überarbeitet …")
        try:
            new_body = await services.claude.revise_article(draft["body_html"], text)
            await services.shopify.update_article(
                draft["shopify_article_gid"], {"body": new_body})
        except (ClaudeError, ShopifyError) as e:
            services.db.log_audit(user_id, "article_edit",
                                  draft["shopify_article_gid"], "error", str(e))
            await update.effective_message.reply_text(f"❌ Fehler: {e}")
            return
        services.db.update_draft(draft_id, body_html=new_body)
        services.db.log_audit(user_id, "article_edit",
                              draft["shopify_article_gid"], "ok", text[:200])
        draft = services.db.get_draft(draft_id)
        await update.effective_message.reply_text(
            preview_text(draft, services.shopify.admin_url(draft["shopify_article_gid"]), []),
            reply_markup=preview_keyboard(draft_id), parse_mode="Markdown",
            disable_web_page_preview=True)


# --- callbacks ---------------------------------------------------------------

@authorized
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = context.bot_data["services"]
    query = update.callback_query
    user_id = update.effective_user.id
    action, arg = parse_cb(query.data)
    await query.answer()

    if action == "menu":
        await query.edit_message_text("📝 Blog — was tun?",
                                      reply_markup=blog_menu_keyboard())
        return

    if action == "new":
        services.db.set_step(user_id, "blog:topic", {})
        await query.edit_message_text(
            "1/3 — Welche schwierige Wandsituation / welches Thema?")
        return

    if action == "design":
        session = services.db.get_session(user_id)
        ctx = session["context"]
        ctx["design"] = DESIGNS.get(arg, "kein bestimmtes Design")
        services.db.set_step(user_id, "blog:must", ctx)
        await query.edit_message_text(
            "3/3 — Was muss unbedingt rein? („-“ für nichts)")
        return

    # --- draft actions: arg is the draft id ---
    draft = services.db.get_draft(arg) if arg else None
    if action in ("pub", "regen", "title", "editdraft", "discard"):
        if draft is None or not draft.get("shopify_article_gid"):
            await query.edit_message_text("⚠️ Dieser Entwurf existiert nicht mehr.",
                                          reply_markup=blog_menu_keyboard())
            return
        gid = draft["shopify_article_gid"]

    if action == "pub":
        try:
            title = chosen_title(draft)
            if title != draft["title_a"]:
                await services.shopify.update_article(gid, {"title": title})
            article = await services.shopify.publish_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "publish", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify-Fehler: {e}")
            return
        services.db.update_draft(arg, status="published")
        services.db.log_audit(user_id, "publish", gid, "ok", article["handle"])
        try:
            blog_handle, _ = await services.shopify.list_articles(
                services.config.blog_id, first=1)
            live = services.shopify.live_url(blog_handle, article["handle"])
        except ShopifyError:
            live = services.shopify.admin_url(gid)
        await query.edit_message_text(
            f"✅ Veröffentlicht: *{chosen_title(draft)}*\n{live}",
            parse_mode="Markdown")

    elif action == "regen":
        session = services.db.get_session(user_id)
        answers = session["context"]
        if not answers.get("topic"):
            await query.edit_message_text(
                "⚠️ Ursprüngliche Angaben fehlen — bitte neu starten.",
                reply_markup=blog_menu_keyboard())
            return
        try:
            await services.shopify.delete_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "regen", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify-Fehler: {e}")
            return
        services.db.delete_draft(arg)
        services.db.log_audit(user_id, "regen", gid, "ok", "old draft deleted")
        await query.edit_message_text("🔄 Alter Entwurf gelöscht.")
        await _create_and_preview(update, context, answers, user_id)

    elif action == "title":
        new_choice = "b" if draft["chosen_title"] == "a" else "a"
        services.db.update_draft(arg, chosen_title=new_choice)
        draft = services.db.get_draft(arg)
        try:
            await services.shopify.update_article(gid, {"title": chosen_title(draft)})
        except ShopifyError as e:
            await query.edit_message_text(f"❌ Shopify-Fehler: {e}")
            return
        services.db.log_audit(user_id, "title_toggle", gid, "ok", new_choice)
        await query.edit_message_text(
            preview_text(draft, services.shopify.admin_url(gid), []),
            reply_markup=preview_keyboard(arg), parse_mode="Markdown",
            disable_web_page_preview=True)

    elif action == "editdraft":
        session = services.db.get_session(user_id)
        ctx = session["context"]
        ctx["draft_id"] = arg
        services.db.set_step(user_id, "blog:editdraft", ctx)
        await query.edit_message_text(
            "✏️ Was soll Claude ändern? (freie Anweisung)")

    elif action == "discard":
        try:
            await services.shopify.delete_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "discard", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify-Fehler: {e}")
            return
        services.db.delete_draft(arg)
        services.db.log_audit(user_id, "discard", gid, "ok", "")
        await query.edit_message_text("🗑 Entwurf verworfen.",
                                      reply_markup=blog_menu_keyboard())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("📝 Blog — was tun?",
                                              reply_markup=blog_menu_keyboard())


def register(app):
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^blog:"))
```

Note for `handle_step("blog:topic")`: it must set step to `None` **and persist the context** (`services.db.set_step(user_id, None, ctx)`), because the design answer arrives via callback, not text.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_blog.py -v` → 7 PASS; whole suite `python -m pytest -v` green.

- [ ] **Step 5: Commit + push**

```bash
git add bot/modules/blog.py tests/test_blog.py
git commit -m "feat: blog module — create/approve/publish loop with draft-first flow"
git push
```

---

### Task 9: Blog module — edit & delete existing articles

**Files:**
- Modify: `bot/modules/blog.py`
- Test: append to `tests/test_blog.py`

**Interfaces:**
- Consumes: `shopify.list_articles/get_article/update_article/delete_article`, `claude.revise_article`.
- Produces: new callback actions — `blog:listedit`, `blog:listdel`, `blog:pickedit:<num>`, `blog:pickdel:<num>`, `blog:confdel:<num>`, `blog:exttitle:<num>`, `blog:extbody:<num>` (`<num>` = numeric article id; gid rebuilt as `gid://shopify/Article/<num>`). New session steps — `blog:exttitle` (new title text), `blog:extbody` (revision instruction); context carries `article_gid`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_blog.py`:

```python
def make_cb_update(data):
    query = SimpleNamespace(data=data, answer=AsyncMock(),
                            edit_message_text=AsyncMock())
    msg = SimpleNamespace(text="", reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=111),
                           callback_query=query, effective_message=msg)

async def test_list_for_delete_shows_articles(services):
    services.shopify.list_articles = AsyncMock(return_value=("news", [
        {"id": "gid://shopify/Article/5", "title": "Alt", "handle": "alt",
         "isPublished": True, "publishedAt": "2026-01-01"}]))
    u = make_cb_update("blog:listdel")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    kb = u.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "blog:pickdel:5"

async def test_delete_requires_confirm_then_deletes(services):
    u = make_cb_update("blog:pickdel:5")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    kb = u.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert any(b.callback_data == "blog:confdel:5"
               for row in kb.inline_keyboard for b in row)
    services.shopify.delete_article.assert_not_awaited()
    u2 = make_cb_update("blog:confdel:5")
    await blog.callbacks.__wrapped__(u2, make_ctx(services))
    services.shopify.delete_article.assert_awaited_once_with("gid://shopify/Article/5")

async def test_edit_existing_title_flow(services):
    u = make_cb_update("blog:exttitle:5")
    await blog.callbacks.__wrapped__(u, make_ctx(services))
    assert services.db.get_session(111)["step"] == "blog:exttitle"
    ut = make_text_update("Neuer Titel")
    await blog.handle_step("blog:exttitle", ut, make_ctx(services))
    services.shopify.update_article.assert_awaited_with(
        "gid://shopify/Article/5", {"title": "Neuer Titel"})
```

(`blog.callbacks.__wrapped__` bypasses the `@authorized` wrapper — auth is already covered by Task 6 tests; add `services.shopify.get_article = AsyncMock(return_value={"id": "gid://shopify/Article/5", "title": "Alt", "handle": "alt", "body": "<p>alt</p>", "isPublished": True})` to the `services` fixture.)

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_blog.py -v` → new tests FAIL.

- [ ] **Step 3: Implement** — add to `bot/modules/blog.py`:

Helper + new branches inside `callbacks` (after the existing draft-action branches):

```python
def article_gid(num: str) -> str:
    return f"gid://shopify/Article/{num}"


def article_list_keyboard(articles, action_prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for a in articles:
        num = a["id"].rsplit("/", 1)[-1]
        mark = "🟢" if a.get("isPublished") else "📝"
        rows.append([InlineKeyboardButton(
            f"{mark} {a['title'][:40]}", callback_data=f"blog:{action_prefix}:{num}")])
    rows.append([InlineKeyboardButton("⬅️ Zurück", callback_data="blog:menu")])
    return InlineKeyboardMarkup(rows)
```

In `callbacks`, add:

```python
    if action in ("listedit", "listdel"):
        try:
            _, articles = await services.shopify.list_articles(
                services.config.blog_id, first=10)
        except ShopifyError as e:
            await query.edit_message_text(f"❌ Shopify-Fehler: {e}")
            return
        if not articles:
            await query.edit_message_text("Keine Artikel gefunden.",
                                          reply_markup=blog_menu_keyboard())
            return
        prefix = "pickedit" if action == "listedit" else "pickdel"
        verb = "bearbeiten" if action == "listedit" else "löschen"
        await query.edit_message_text(f"Welchen Artikel {verb}?",
                                      reply_markup=article_list_keyboard(articles, prefix))
        return

    if action == "pickedit":
        await query.edit_message_text(
            "Was ändern?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Titel ändern",
                                      callback_data=f"blog:exttitle:{arg}")],
                [InlineKeyboardButton("Mit Claude überarbeiten",
                                      callback_data=f"blog:extbody:{arg}")],
                [InlineKeyboardButton("⬅️ Zurück", callback_data="blog:listedit")],
            ]))
        return

    if action in ("exttitle", "extbody"):
        session = services.db.get_session(user_id)
        ctx = session["context"]
        ctx["article_gid"] = article_gid(arg)
        services.db.set_step(user_id, f"blog:{action}", ctx)
        prompt = ("✏️ Neuer Titel?" if action == "exttitle"
                  else "✏️ Was soll Claude ändern? (freie Anweisung)")
        await query.edit_message_text(prompt)
        return

    if action == "pickdel":
        await query.edit_message_text(
            "⚠️ Wirklich löschen? Das kann nicht rückgängig gemacht werden.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Ja, endgültig löschen",
                                      callback_data=f"blog:confdel:{arg}")],
                [InlineKeyboardButton("⬅️ Abbrechen", callback_data="blog:listdel")],
            ]))
        return

    if action == "confdel":
        gid = article_gid(arg)
        try:
            await services.shopify.delete_article(gid)
        except ShopifyError as e:
            services.db.log_audit(user_id, "article_delete", gid, "error", str(e))
            await query.edit_message_text(f"❌ Shopify-Fehler: {e}")
            return
        services.db.log_audit(user_id, "article_delete", gid, "ok", "")
        await query.edit_message_text("🗑 Artikel gelöscht.",
                                      reply_markup=blog_menu_keyboard())
        return
```

In `handle_step`, add branches:

```python
    elif step == "blog:exttitle":
        gid = ctx.get("article_gid")
        services.db.set_step(user_id, None, ctx)
        try:
            await services.shopify.update_article(gid, {"title": text})
        except ShopifyError as e:
            services.db.log_audit(user_id, "article_edit", gid, "error", str(e))
            await update.effective_message.reply_text(f"❌ Shopify-Fehler: {e}")
            return
        services.db.log_audit(user_id, "article_edit", gid, "ok", f"title={text[:80]}")
        await update.effective_message.reply_text("✅ Titel geändert.",
                                                  reply_markup=blog_menu_keyboard())
    elif step == "blog:extbody":
        gid = ctx.get("article_gid")
        services.db.set_step(user_id, None, ctx)
        await update.effective_message.reply_text("✍️ Claude überarbeitet …")
        try:
            article = await services.shopify.get_article(gid)
            new_body = await services.claude.revise_article(article["body"], text)
            await services.shopify.update_article(gid, {"body": new_body})
        except (ClaudeError, ShopifyError) as e:
            services.db.log_audit(user_id, "article_edit", gid, "error", str(e))
            await update.effective_message.reply_text(f"❌ Fehler: {e}")
            return
        services.db.log_audit(user_id, "article_edit", gid, "ok", text[:200])
        await update.effective_message.reply_text("✅ Artikel überarbeitet.",
                                                  reply_markup=blog_menu_keyboard())
```

- [ ] **Step 4: Run tests** — `python -m pytest -v` → whole suite PASS.

- [ ] **Step 5: Commit + push**

```bash
git add bot/modules/blog.py tests/test_blog.py
git commit -m "feat: blog module — edit and delete existing articles"
git push
```

---

### Task 10: README + deployment (systemd, deploy script)

**Files:**
- Create: `deploy/amma-bot.service`, `deploy/deploy.sh`, `README.md`

**Interfaces:** none (docs + ops).

- [ ] **Step 1: Create** `deploy/amma-bot.service`:

```ini
[Unit]
Description=AMAwalls Ops Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=amma
WorkingDirectory=/opt/amma_bot
ExecStart=/opt/amma_bot/.venv/bin/python -m bot.main
Restart=always
RestartSec=5
# .env is read by python-dotenv from WorkingDirectory; no EnvironmentFile needed

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create** `deploy/deploy.sh`:

```bash
#!/usr/bin/env bash
# Deploy to arturserver. Excludes secrets, venv, and the database.
set -euo pipefail

HOST="${1:-arturserver}"
DEST="/opt/amma_bot"

rsync -az --delete \
  --exclude '.env' --exclude '*.db' --exclude '.venv' \
  --exclude '.git' --exclude '__pycache__' --exclude '.pytest_cache' \
  ./ "$HOST:$DEST/"

ssh "$HOST" "cd $DEST \
  && [ -d .venv ] || python3 -m venv .venv \
  && .venv/bin/pip install -q -r requirements.txt \
  && sudo cp deploy/amma-bot.service /etc/systemd/system/ \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now amma-bot \
  && sudo systemctl restart amma-bot \
  && systemctl --no-pager status amma-bot | head -5"

echo "Deployed. Remember: $DEST/.env must exist on the server (copy .env.example and fill it)."
```

`chmod +x deploy/deploy.sh`

- [ ] **Step 3: Write** `README.md` covering exactly:
  - What the bot is (1 paragraph).
  - Setup: clone, `python3 -m venv .venv`, `pip install -r requirements-dev.txt`, `cp .env.example .env` + fill every key (table of keys with one-line descriptions matching `.env.example` comments), where to get each secret (BotFather, Shopify custom app → Admin API token with `read_content`/`write_content` scopes, blog GID via `{ blogs(first: 5) { nodes { id title } } }` in the Shopify GraphiQL app, Telegram user id via @userinfobot).
  - Verify: `python -m pytest`, then `python scripts/smoke_test.py` (must print `SMOKE TEST PASSED` before first run).
  - Run: `python -m bot.main` (polling default; `TRANSPORT=webhook` + `WEBHOOK_URL`/`WEBHOOK_PORT` for webhook mode).
  - Deploy: `./deploy/deploy.sh arturserver`, plus the one-time server steps (create `amma` user, create `/opt/amma_bot`, copy+fill `.env` on the server).
  - **How to add a new module** section: create `bot/modules/<name>.py` exposing `NAME`, `MENU_LABEL`, `register(app)`, `handle_step(step, update, context)`; namespace all `callback_data` and steps as `<name>:…`; add it to the `modules=[...]` list in `bot/main.py:main()`. Nothing else changes.
  - Going live: swap `SHOPIFY_STORE_DOMAIN` + `SHOPIFY_ADMIN_TOKEN` (+ `BLOG_ID`) in `.env`, restart the service.

- [ ] **Step 4: Commit + push**

```bash
git add deploy README.md
git commit -m "docs: README + systemd unit and deploy script"
git push
```

---

### Task 11: Live E2E against the dev store (checkpoint — needs user)

**Files:** none (verification only).

- [ ] **Step 1:** User fills `.env` (BOT_PASSWORD, ALLOWLIST_USER_IDS, ANTHROPIC_API_KEY, SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_TOKEN, BLOG_ID). TELEGRAM_BOT_TOKEN is already set.
- [ ] **Step 2:** `python scripts/smoke_test.py` → `SMOKE TEST PASSED`.
- [ ] **Step 3:** `python -m bot.main`, then in Telegram: `/start` → password → Blog → Neuer Artikel → answer 3 questions → verify draft appears in Shopify admin as unpublished → tap **Publish** → verify article is live. This demonstrates the create → approve → publish loop required before edit/delete sign-off.
- [ ] **Step 4:** Verify a non-allowlisted account gets **no reply** to `/start`.
- [ ] **Step 5:** Check `audit_log` rows exist: `sqlite3 amma_bot.db "SELECT action, result FROM audit_log ORDER BY id DESC LIMIT 10;"`
