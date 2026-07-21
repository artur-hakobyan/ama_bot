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
