import sqlite3

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

def test_update_draft_with_no_fields_raises_error(db):
    did = db.create_draft(1, "a", "b", "c", "d", [])
    with pytest.raises(ValueError):
        db.update_draft(did)

def test_update_draft_rejects_invalid_chosen_title(db):
    did = db.create_draft(1, "a", "b", "c", "d", [])
    with pytest.raises(ValueError):
        db.update_draft(did, chosen_title="z")

def test_audit(db):
    db.log_audit(42, "publish", "gid://shopify/Article/1", "ok", "live")
    rows = db._conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1 and rows[0]["action"] == "publish"


def test_keyword_usage_tracking(db):
    assert db.used_keywords() == set()
    db.mark_keyword_used("Absorber Büro", "Akustik im Büro", "gid://shopify/Article/1")
    db.mark_keyword_used("akustik büro", "Akustik im Büro")
    used = db.used_keywords()
    assert used == {"absorber büro", "akustik büro"}   # normalised to lowercase
    db.mark_keyword_used("Absorber Büro", "Akustik im Büro")   # idempotent
    assert len(db.used_keywords()) == 2


def test_batch_lifecycle(db):
    proposals = [{"keyword": "absorber büro", "title": "T1"},
                 {"keyword": "akustik büro", "title": "T2"}]
    bid = db.create_batch(42, "Akustik im Büro", proposals)
    b = db.get_batch(bid)
    assert b["proposals"] == proposals and b["current_index"] == 0
    assert b["status"] == "proposed"

    assert db.active_batch(42)["id"] == bid          # findable while open
    db.update_batch(bid, current_index=1, status="running")
    assert db.get_batch(bid)["current_index"] == 1

    db.update_batch(bid, status="done")
    assert db.active_batch(42) is None               # finished batches drop out


def test_update_batch_rejects_unknown_column(db):
    bid = db.create_batch(1, "P", [])
    import pytest
    with pytest.raises(ValueError):
        db.update_batch(bid, evil="x")


# --- schema migration -------------------------------------------------------

def test_a_database_missing_newer_columns_is_migrated(tmp_path):
    """A live run lost its Google Doc link to "no such column: doc_url".

    The deployed database was created before doc_url and doc_file_id existed,
    and CREATE TABLE IF NOT EXISTS left it untouched.
    """
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE drafts (
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
    """)
    conn.commit()
    conn.close()

    db = Database(str(path))
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(drafts)")}
    assert "doc_url" in cols
    assert "doc_file_id" in cols

    # And the migrated database must actually accept a write to them.
    draft_id = db.create_draft(1, "A", "B", "<p>x</p>", "sum", ["t"])
    db.update_draft(draft_id, doc_url="https://docs.google.com/d/x",
                    doc_file_id="fileid")
    assert db.get_draft(draft_id)["doc_url"] == "https://docs.google.com/d/x"
    db.close()


def test_migrating_twice_is_harmless(tmp_path):
    path = tmp_path / "x.db"
    Database(str(path)).close()
    db = Database(str(path))          # every restart runs _migrate again
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(drafts)")}
    assert "doc_url" in cols
    db.close()


def test_existing_rows_survive_the_migration(tmp_path):
    path = tmp_path / "keep.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE drafts (
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
    """)
    conn.execute("INSERT INTO drafts (id, user_id, title_a) VALUES ('keep', 7, 'Old')")
    conn.commit()
    conn.close()

    db = Database(str(path))
    row = db.get_draft("keep")
    assert row["title_a"] == "Old"
    assert row["doc_url"] is None
    db.close()
