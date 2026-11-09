"""Keyword rotation: every keyword in a pillar is used once before any repeats.

Until 2026-11 a keyword only counted as used once its article was published, so
every unpublished draft left its keyword at the top of the list: "akustische
bilder" was written four times and "akustikbild aufbau" three, while the rest of
the sheet sat untouched. Supporting keywords were simply the top eight by
volume, so the same handful appeared in every article.
"""
from bot.db import Database
from bot.keywords import Keyword, KeywordSheet, LiveKeywordSheet


def kw(name, volume, pillar="P"):
    return Keyword(keyword=name, volume=volume, competition="Niedrig",
                   competition_index=0, pillar=pillar)


class FakeSheet(KeywordSheet):
    def __init__(self, data: dict):
        self._data = data

    @property
    def pillars(self):
        return list(self._data)

    def keywords(self, pillar):
        return self._data[pillar]


SHEET = FakeSheet({"P": [kw("alpha", 5000), kw("beta", 1000), kw("gamma", 500)],
                   "Q": [kw("delta", 100, "Q")]})


# --- ranking ------------------------------------------------------------------

def test_unused_keywords_come_first_best_volume_first():
    ranked = SHEET.ranked("P", {"alpha": 1})
    assert [k.keyword for k in ranked] == ["beta", "gamma", "alpha"]


def test_a_round_ends_only_when_every_keyword_was_used():
    uses = {"alpha": 1, "beta": 1}
    assert SHEET.round_of("P", uses) == 1
    assert SHEET.remaining("P", uses) == 1          # gamma is still open
    uses["gamma"] = 1
    assert SHEET.round_of("P", uses) == 2           # round two starts …
    assert SHEET.remaining("P", uses) == 3
    assert SHEET.ranked("P", uses)[0].keyword == "alpha"   # … from the best again


def test_a_pillar_is_never_exhausted_it_starts_over():
    uses = {"alpha": 3, "beta": 3, "gamma": 3}
    assert [k.keyword for k in SHEET.ranked("P", uses)] == ["alpha", "beta", "gamma"]


def test_keywords_already_in_the_batch_are_excluded():
    ranked = SHEET.ranked("P", {}, exclude={"alpha"})
    assert [k.keyword for k in ranked] == ["beta", "gamma"]


def test_supporting_candidates_rotate_and_skip_the_focus():
    pool = SHEET.supporting_candidates("P", {"beta": 2}, exclude={"alpha"})
    assert pool == ["gamma", "beta"]


def test_a_plain_set_still_works_as_usage():
    assert SHEET.ranked("P", {"alpha"})[0].keyword == "beta"


def test_the_local_workbook_cannot_be_marked():
    assert SHEET.mark_usage({"alpha": {"focus": "x", "supporting": ""}}) == 0


# --- database -----------------------------------------------------------------

def test_usage_is_counted_per_role(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    d1 = db.create_draft(1, "T1", "T1b", "<p>x</p>", "s", [])
    d2 = db.create_draft(1, "T2", "T2b", "<p>x</p>", "s", [])
    db.record_keyword_use("Alpha", "P", "focus", d1)
    db.record_keyword_use("beta", "P", "supporting", d1)
    db.record_keyword_use("alpha", "P", "focus", d2)
    assert db.keyword_use_counts() == {"alpha": 2}
    assert db.keyword_use_counts("supporting") == {"beta": 1}
    assert sorted(db.keywords_of_draft(d1)) == ["alpha", "beta"]
    db.close()


def test_a_discarded_draft_gives_its_keywords_back(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    d1 = db.create_draft(1, "T1", "T1b", "<p>x</p>", "s", [])
    db.record_keyword_use("alpha", "P", "focus", d1)
    db.delete_draft(d1)
    assert db.keyword_use_counts() == {}
    db.close()


def test_usage_lines_show_title_date_and_status(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    d1 = db.create_draft(1, "Erster", "Zweiter", "<p>x</p>", "s", [])
    db.record_keyword_use("alpha", "P", "focus", d1)
    db.record_keyword_use("alpha", "P", "supporting", d1)
    marks = db.keyword_marks(["Alpha", "unused"])
    assert "Erster" in marks["alpha"]["focus"]
    assert "draft" in marks["alpha"]["focus"]
    assert marks["unused"] == {"focus": "", "supporting": ""}   # clears the cell
    db.update_draft(d1, status="published", chosen_title="b")
    marks = db.keyword_marks(["alpha"])
    assert "Zweiter" in marks["alpha"]["focus"]
    assert "published" in marks["alpha"]["focus"]
    db.close()


def test_existing_history_is_backfilled(tmp_path):
    """Drafts written before rotation existed must count, or round one would
    hand out the over-used keywords yet again."""
    path = str(tmp_path / "t.db")
    db = Database(path)
    d1 = db.create_draft(1, "Alt", "Alt b", "<p>x</p>", "s", [])
    db.update_draft(d1, image_keyword="akustikbild aufbau",
                    image_pillar="Grundlagen")
    db._conn.execute("INSERT INTO keywords_used (keyword, pillar) VALUES (?, ?)",
                     ("akustik design", "Grundlagen"))
    db._conn.execute("DELETE FROM keyword_uses")
    db._conn.commit()
    db.close()

    db = Database(path)
    assert db.keyword_use_counts() == {"akustikbild aufbau": 1, "akustik design": 1}
    db.close()


# --- marking the live sheet ---------------------------------------------------

class FakeGoogle:
    def __init__(self, tabs):
        self.tabs = tabs
        self.writes = []

    def sheet_tabs(self, sid):
        return list(self.tabs)

    def sheet_values(self, sid, tab):
        return self.tabs[tab]

    def sheet_write(self, sid, data):
        self.writes.extend(data)


HEADER = ["Keyword", "Currency", "Avg. monthly searches"]


def test_marks_land_in_every_tab_that_lists_the_keyword():
    g = FakeGoogle({
        "All Keywords": [HEADER + ["Pillar-Topics"], ["alpha", "EUR", "5000", "P"],
                         ["beta", "EUR", "10", "P"]],
        "P": [HEADER, ["Alpha ", "EUR", "5000"], [], ["beta", "EUR", "10"]],
    })
    sheet = LiveKeywordSheet(g, "sid")
    n = sheet.mark_usage({"alpha": {"focus": "Titel (2026-11-09, draft)",
                                    "supporting": ""}})
    cells = {w["range"]: w["values"][0][0] for w in g.writes}
    # Headers are added once, right after the existing columns.
    assert cells["'All Keywords'!E1"] == "Used in blog (focus)"
    assert cells["'All Keywords'!F1"] == "Used in blog (supporting)"
    assert cells["'P'!D1"] == "Used in blog (focus)"
    assert cells["'All Keywords'!E2"] == "Titel (2026-11-09, draft)"
    assert cells["'P'!D2"] == "Titel (2026-11-09, draft)"
    assert "'P'!D4" not in cells                    # beta was not touched
    assert n == 2


def test_existing_mark_columns_are_reused():
    g = FakeGoogle({"P": [HEADER + ["Used in blog (focus)", "Used in blog (supporting)"],
                          ["alpha", "EUR", "5000", "old", ""]]})
    LiveKeywordSheet(g, "sid").mark_usage(
        {"alpha": {"focus": "neu", "supporting": "x"}})
    cells = {w["range"]: w["values"][0][0] for w in g.writes}
    assert cells == {"'P'!D2": "neu", "'P'!E2": "x"}


def test_mark_columns_are_not_mistaken_for_keyword_data():
    g = FakeGoogle({"P": [HEADER + ["Used in blog (focus)"],
                          ["alpha", "EUR", "5000", "Titel"]]})
    ks = LiveKeywordSheet(g, "sid").keywords("P")
    assert [k.keyword for k in ks] == ["alpha"] and ks[0].volume == 5000


# --- the blog flow ------------------------------------------------------------

from types import SimpleNamespace                                    # noqa: E402
from unittest.mock import AsyncMock                                  # noqa: E402

from bot.modules import blog                                         # noqa: E402
from tests.test_blog import make_cb_update, make_ctx, services       # noqa: E402,F401


class MarkingSheet(FakeSheet):
    def __init__(self, data):
        super().__init__(data)
        self.marked = []

    def mark_usage(self, marks):
        self.marked.append(marks)
        return len(marks)


BIG = MarkingSheet({
    "Grundlagen": [kw(f"g{n}", 1000 - n, "Grundlagen") for n in range(4)],
    "Büro": [kw(f"b{n}", 5000 - n, "Büro") for n in range(10)],
})


def test_supporting_keywords_stay_in_the_pool_and_are_not_shared():
    proposals = [
        {"keyword": "g0", "supporting_keywords": ["erfunden", "s1", "s2"]},
        {"keyword": "g1", "supporting_keywords": ["s1", "s3", "G0"]},
    ]
    pool = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10"]
    blog.settle_supporting(proposals, pool)
    first, second = (p["supporting_keywords"] for p in proposals)
    assert "erfunden" not in first                       # not in the sheet
    assert first[:2] == ["s1", "s2"]                     # the model's picks kept
    assert "s1" not in second and "g0" not in second     # no sharing, no focus
    assert len(first) >= blog.SUPPORTING_MIN and len(second) >= blog.SUPPORTING_MIN


def test_a_small_pool_is_shared_rather_than_leaving_articles_short():
    proposals = [{"keyword": "a", "supporting_keywords": []},
                 {"keyword": "b", "supporting_keywords": []}]
    blog.settle_supporting(proposals, ["x1", "x2", "x3", "x4", "x5", "x6"])
    assert all(len(p["supporting_keywords"]) == 5 for p in proposals)


def test_the_next_pillar_is_the_one_furthest_behind(services):
    services.keywords = BIG
    assert blog._next_pillar(services) == "Grundlagen"       # sheet order on a tie
    services.db.record_keyword_use("g0", "Grundlagen", "focus", "d1")
    # 1 of 4 done in Grundlagen vs 0 of 10 in Büro: Büro is behind now.
    assert blog._next_pillar(services) == "Büro"


async def test_a_new_batch_moves_on_to_unwritten_keywords(services, monkeypatch):
    services.keywords = BIG
    for n in range(3):
        services.db.record_keyword_use(f"b{n}", "Büro", "focus", f"d{n}")
    seen = {}

    async def fake_propose(claude, picks, pillar, rules, **kwargs):
        seen["picks"] = [k.keyword for k in picks]
        seen["pool"] = kwargs.get("supporting_pool")
        return [{"keyword": k.keyword, "title": f"T {k.keyword}", "outline": [],
                 "supporting_keywords": ["b9"], "value": ""} for k in picks]

    async def no_titles(services):
        return []

    monkeypatch.setattr(blog, "propose_articles", fake_propose)
    monkeypatch.setattr(blog, "_published_titles", no_titles)
    status = SimpleNamespace(edit_text=AsyncMock())
    msg = SimpleNamespace(reply_text=AsyncMock(return_value=status))
    update = SimpleNamespace(effective_message=msg)
    await blog._start_batch(update, make_ctx(services), 111, "Büro")

    assert seen["picks"] == ["b3", "b4", "b5"]          # not b0–b2 again
    assert seen["pool"] and not set(seen["pool"]) & {"b3", "b4", "b5"}
    batch = services.db.active_batch(111)
    assert all(set(p["supporting_keywords"]) <= set(seen["pool"])
               for p in batch["proposals"])


async def test_discarding_a_draft_releases_its_keywords_in_the_sheet(services):
    services.keywords = MarkingSheet({"P": [kw("alpha", 10)]})
    did = services.db.create_draft(111, "T", "Tb", "<p>x</p>", "s", [])
    services.db.update_draft(did, shopify_article_gid="gid://shopify/Article/9")
    await blog._record_keywords(services, 111, did, "P", "alpha", ["beta"], "T")
    assert services.db.keyword_use_counts() == {"alpha": 1}
    assert "T (" in services.keywords.marked[-1]["alpha"]["focus"]

    await blog.callbacks.__wrapped__(make_cb_update(f"blog:discard:{did}"),
                                     make_ctx(services))
    assert services.db.keyword_use_counts() == {}
    assert services.keywords.marked[-1]["alpha"] == {"focus": "", "supporting": ""}


async def test_an_unreachable_sheet_does_not_lose_the_article(services):
    class Broken(FakeSheet):
        def mark_usage(self, marks):
            raise RuntimeError("quota")

    services.keywords = Broken({"P": [kw("alpha", 10)]})
    did = services.db.create_draft(111, "T", "Tb", "<p>x</p>", "s", [])
    await blog._record_keywords(services, 111, did, "P", "alpha", [], "T")
    assert services.db.keyword_use_counts() == {"alpha": 1}
    rows = [r for r in services.db.recent_audit(10)
            if r["action"] == "keyword_sheet_mark"]
    assert rows and rows[0]["result"] == "error"
