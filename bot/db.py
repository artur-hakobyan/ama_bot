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
