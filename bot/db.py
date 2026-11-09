import json
import re
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
  doc_url TEXT,
  doc_file_id TEXT,
  image_keyword TEXT,
  image_pillar TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keywords_used (
  keyword TEXT PRIMARY KEY,
  pillar TEXT NOT NULL,
  article_gid TEXT,
  used_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keyword_uses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  pillar TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'focus',
  draft_id TEXT,
  title TEXT,
  used_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  pillar TEXT NOT NULL,
  proposals_json TEXT NOT NULL DEFAULT '[]',
  current_index INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'proposed',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS images_used (
  file_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  draft_id TEXT,
  used_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    "title_a", "title_b", "body_html", "summary", "doc_url", "doc_file_id",
    "image_keyword", "image_pillar",
}


class Database:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._backfill_keyword_uses()
        self._conn.commit()

    def _migrate(self):
        """Bring an older database up to the current schema.

        CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so a
        deployed database keeps whatever shape it was created with and every
        write to a newer column fails at runtime. This reads the columns the
        schema declares and adds the ones the file is missing, so a column added
        to SCHEMA can never be forgotten here — the earlier hand-listed version
        missed doc_url and lost a Google Doc link on a live run.

        Only additive changes are handled; SQLite cannot drop or retype a column
        without rebuilding the table, and nothing here needs that.
        """
        for table, columns in self._schema_columns().items():
            existing = {r["name"] for r in
                        self._conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue            # table did not exist; executescript made it
            for name, ddl in columns.items():
                if name not in existing:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    @staticmethod
    def _schema_columns() -> dict:
        """Parse SCHEMA into {table: {column: type}}.

        Columns carrying a non-constant default are skipped: SQLite refuses to
        ALTER those in, and every one of them is already present in any database
        old enough to need migrating.
        """
        tables = {}
        for block in re.finditer(
                r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);",
                SCHEMA, re.S):
            name, body = block.group(1), block.group(2)
            cols = {}
            for line in body.strip().splitlines():
                line = line.strip().rstrip(",")
                if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN",
                                                        "UNIQUE", "CHECK")):
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                col, ddl = parts[0], parts[1]
                if "PRIMARY KEY" in ddl.upper() or "(" in ddl:
                    continue        # rowid aliases and expression defaults
                cols[col] = ddl
            tables[name] = cols
        return tables

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
        if not fields:
            raise ValueError("update_draft called with no fields")
        unknown = set(fields) - DRAFT_COLUMNS
        if unknown:
            raise ValueError(f"Unknown draft columns: {unknown}")
        if "chosen_title" in fields and fields["chosen_title"] not in ("a", "b"):
            raise ValueError(f"chosen_title must be 'a' or 'b', got {fields['chosen_title']!r}")
        sets = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(
            f"UPDATE drafts SET {sets} WHERE id = ?",
            (*fields.values(), draft_id))
        self._conn.commit()

    def delete_draft(self, draft_id: str):
        """Remove a draft. Its keywords go back into rotation: the article they
        were spent on no longer exists."""
        self._conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        self._conn.execute("DELETE FROM keyword_uses WHERE draft_id = ?",
                           (draft_id,))
        self._conn.commit()

    # --- keyword inventory ---
    # A keyword counts as used the moment an article is written with it, not
    # when it is published: counting only published ones let every unpublished
    # draft hand its keyword out again, and the same three were written over
    # and over. `role` is "focus" or "supporting"; they rotate independently.

    def record_keyword_use(self, keyword: str, pillar: str, role: str,
                           draft_id: str = None, title: str = ""):
        self._conn.execute(
            "INSERT INTO keyword_uses (keyword, pillar, role, draft_id, title)"
            " VALUES (?, ?, ?, ?, ?)",
            (keyword.strip().lower(), pillar or "", role, draft_id, title or ""))
        self._conn.commit()

    def keyword_use_counts(self, role: str = "focus") -> dict:
        """lowercase keyword -> number of articles that used it in this role."""
        return {r["keyword"]: r["n"] for r in self._conn.execute(
            "SELECT keyword, COUNT(*) AS n FROM keyword_uses WHERE role = ?"
            " GROUP BY keyword", (role,))}

    def used_keywords(self) -> set:
        """Every keyword that has been the focus of at least one article."""
        return set(self.keyword_use_counts("focus"))

    def keywords_of_draft(self, draft_id: str) -> list:
        return [r["keyword"] for r in self._conn.execute(
            "SELECT DISTINCT keyword FROM keyword_uses WHERE draft_id = ?",
            (draft_id,))]

    def keyword_marks(self, keywords) -> dict:
        """The text the keyword sheet shows for each keyword, per role.

        One line per article: "Title (2026-11-09, published)". The title and
        status come from the draft as it is now, so the chosen B-title and a
        later publish show up when the marks are refreshed. A keyword with no
        uses maps to empty texts, which clears its cells.
        """
        keys = sorted({k.strip().lower() for k in keywords if k and k.strip()})
        marks = {k: {"focus": [], "supporting": []} for k in keys}
        if not keys:
            return {}
        placeholders = ",".join("?" * len(keys))
        for r in self._conn.execute(
                "SELECT u.keyword, u.role, u.used_at, u.title AS used_title,"
                " d.title_a, d.title_b, d.chosen_title, d.status"
                " FROM keyword_uses u LEFT JOIN drafts d ON d.id = u.draft_id"
                f" WHERE u.keyword IN ({placeholders}) ORDER BY u.used_at, u.id",
                keys):
            if r["status"] is not None:
                title = r["title_b"] if r["chosen_title"] == "b" else r["title_a"]
                state = "published" if r["status"] == "published" else "draft"
            else:
                title, state = r["used_title"], "published"
            line = f"{title or 'earlier article'} ({r['used_at'][:10]}, {state})"
            marks[r["keyword"]].setdefault(r["role"], []).append(line)
        return {k: {"focus": "\n".join(v["focus"]),
                    "supporting": "\n".join(v["supporting"])}
                for k, v in marks.items()}

    def _backfill_keyword_uses(self):
        """Seed usage from the history kept before rotation existed.

        Runs while the table is empty. Drafts carry their focus keyword in
        image_keyword; the older keywords_used table holds published keywords
        from before that column existed.
        """
        if self._conn.execute("SELECT 1 FROM keyword_uses LIMIT 1").fetchone():
            return
        self._conn.execute(
            "INSERT INTO keyword_uses (keyword, pillar, role, draft_id, title, used_at)"
            " SELECT lower(trim(image_keyword)), COALESCE(image_pillar, ''), 'focus',"
            " id, title_a, created_at FROM drafts"
            " WHERE image_keyword IS NOT NULL AND trim(image_keyword) != ''")
        self._conn.execute(
            "INSERT INTO keyword_uses (keyword, pillar, role, title, used_at)"
            " SELECT keyword, pillar, 'focus', '', used_at FROM keywords_used"
            " WHERE keyword NOT IN (SELECT keyword FROM keyword_uses)")

    def next_article_number(self) -> int:
        """Sequential blog number used in the Google Doc filename."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE doc_url IS NOT NULL").fetchone()
        return (row["n"] or 0) + 1

    # --- weekly batches ---
    def create_batch(self, user_id: int, pillar: str, proposals: list) -> str:
        batch_id = secrets.token_hex(4)
        self._conn.execute(
            "INSERT INTO batches (id, user_id, pillar, proposals_json)"
            " VALUES (?, ?, ?, ?)",
            (batch_id, user_id, pillar, json.dumps(proposals)))
        self._conn.commit()
        return batch_id

    def get_batch(self, batch_id: str):
        row = self._conn.execute(
            "SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            return None
        b = dict(row)
        b["proposals"] = json.loads(b.pop("proposals_json"))
        return b

    def update_batch(self, batch_id: str, **fields):
        allowed = {"current_index", "status", "proposals_json"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown batch columns: {unknown}")
        if not fields:
            raise ValueError("update_batch called with no fields")
        sets = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(f"UPDATE batches SET {sets} WHERE id = ?",
                           (*fields.values(), batch_id))
        self._conn.commit()

    def has_started(self, user_id: int) -> bool:
        """Whether this user has ever opened the bot.

        Telegram refuses a bot's first message ("Chat not found"), so a
        scheduled batch can only reach someone with a session row.
        """
        return self._conn.execute(
            "SELECT 1 FROM sessions WHERE user_id = ?",
            (user_id,)).fetchone() is not None

    def active_batch(self, user_id: int):
        row = self._conn.execute(
            "SELECT id FROM batches WHERE user_id = ? AND status IN"
            " ('proposed','running') ORDER BY created_at DESC LIMIT 1",
            (user_id,)).fetchone()
        return self.get_batch(row["id"]) if row else None

    def open_batches(self) -> list:
        """Every operator's unfinished batch, for the shared team view."""
        import json as _json
        out = []
        for r in self._conn.execute(
                "SELECT id, user_id, pillar, current_index, proposals_json"
                " FROM batches WHERE status IN ('proposed','running')"
                " ORDER BY created_at"):
            d = dict(r)
            d["total"] = len(_json.loads(d.pop("proposals_json")))
            out.append(d)
        return out

    def pending_drafts(self) -> list:
        """Drafts nobody has published yet, across the whole team."""
        return [dict(r) for r in self._conn.execute(
            "SELECT id, user_id, title_a, doc_url FROM drafts"
            " WHERE status = 'pending' ORDER BY created_at DESC")]

    # --- image inventory ---
    def mark_image_used(self, file_id: str, name: str, draft_id: str = ""):
        self._conn.execute(
            "INSERT OR REPLACE INTO images_used (file_id, name, draft_id)"
            " VALUES (?, ?, ?)", (file_id, name, draft_id))
        self._conn.commit()

    def used_images(self) -> set:
        """File ids already attached to an article, so they are not re-offered."""
        return {r["file_id"] for r in
                self._conn.execute("SELECT file_id FROM images_used")}

    # --- audit ---
    def recent_audit(self, limit: int = 20) -> list:
        """Newest audit entries first — for /health and for tests."""
        return [dict(r) for r in self._conn.execute(
            "SELECT user_id, action, target, result, detail, created_at"
            " FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]

    def log_audit(self, user_id, action, target, result, detail=""):
        self._conn.execute(
            "INSERT INTO audit_log (user_id, action, target, result, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, action, target, result, detail))
        self._conn.commit()
