"""Three review points from the operators' colleagues.

5. The draft should arrive without image suggestions; images come after the
   article itself is approved.
6. "Applying comments" claimed one minute and took far longer.
7. The bot is used by three people who could not see each other's work.
"""
import pytest

from bot.db import Database


# --- point 5: images after approval ----------------------------------------

def test_the_draft_records_what_its_images_need(tmp_path):
    """Images are offered later, so the keyword must survive until then."""
    db = Database(str(tmp_path / "t.db"))
    d = db.create_draft(1, "A", "B", "<p>x</p>", "s", ["t"])
    db.update_draft(d, image_keyword="akustische bilder",
                    image_pillar="Grundlagen")
    row = db.get_draft(d)
    assert row["image_keyword"] == "akustische bilder"
    assert row["image_pillar"] == "Grundlagen"
    db.close()


def test_the_preview_no_longer_triggers_images():
    """The article preview must be a single decision, not two at once."""
    import inspect

    from bot.modules import blog

    src = inspect.getsource(blog._write_from_keyword)
    assert "_offer_images" not in src, \
        "images are still offered beside the draft"


def test_images_are_offered_once_the_article_is_published():
    import inspect

    from bot.modules import blog

    src = inspect.getsource(blog.callbacks)
    publish = src.split('if action == "pub"')[1].split("elif action ==")[0]
    assert "_offer_images" in publish, "images are never offered after approval"


# --- point 6: honest waiting time ------------------------------------------

def test_the_comment_estimate_matches_the_measured_time():
    """It claimed a minute; a live run took over two."""
    import inspect

    from bot.modules import blog

    src = inspect.getsource(blog.callbacks)
    notes = src.split('elif action == "docnotes"')[1].split("elif action ==")[0]
    assert "eta_seconds=180" in notes, "the estimate is still too optimistic"


def test_the_estimate_is_rendered_in_minutes():
    from bot.modules.blog import eta_line

    assert eta_line(180) == "about 3 minutes"


# --- point 7: shared team state --------------------------------------------

def test_rules_are_not_split_per_user():
    """The learning loop is team-wide: one person's correction teaches all."""
    import inspect

    from bot import house_rules

    assert "user_id" not in inspect.getsource(house_rules), \
        "house rules became per-user, breaking shared learning"


def test_used_keywords_and_images_are_shared(tmp_path):
    """Two operators must not be handed the same keyword or mockup."""
    db = Database(str(tmp_path / "t.db"))
    db.mark_keyword_used("akustische bilder", "Grundlagen", "gid1")
    db.mark_image_used("file1", "mockup.jpg", "draft1")
    # Queried without a user id at all — that is what makes them shared.
    assert "akustische bilder" in db.used_keywords()
    assert "file1" in db.used_images()
    db.close()


def test_open_batches_span_the_whole_team(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.create_batch(111, "Grundlagen", [{"keyword": "a"}, {"keyword": "b"}])
    db.create_batch(222, "Akustik im Büro", [{"keyword": "c"}])
    rows = db.open_batches()
    assert {r["user_id"] for r in rows} == {111, 222}
    assert {r["total"] for r in rows} == {2, 1}
    db.close()


def test_pending_drafts_span_the_whole_team(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.create_draft(111, "Mine", "B", "<p>x</p>", "s", [])
    other = db.create_draft(222, "Theirs", "B", "<p>x</p>", "s", [])
    db.update_draft(other, status="published")
    rows = db.pending_drafts()
    assert [r["title_a"] for r in rows] == ["Mine"]
    db.close()


async def test_a_teammate_who_never_started_is_skipped():
    """Telegram refuses a bot's first message; that must not break the action."""
    from bot.modules.blog import notify_team

    sent = []

    class Bot:
        async def send_message(self, uid, text, **kw):
            sent.append(uid)

    class Cfg:
        allowlist_user_ids = frozenset({1, 2, 3})

    class DB:
        def has_started(self, uid):
            return uid != 3          # 3 never opened the bot

    class S:
        config = Cfg()
        db = DB()

    class Ctx:
        bot = Bot()

    await notify_team(Ctx(), S(), actor_id=1, text="hi")
    assert sent == [2], "notified the wrong people"


async def test_a_failed_notification_never_breaks_the_action():
    """Publishing must succeed even if a teammate blocked the bot."""
    from bot.modules.blog import notify_team

    class Bot:
        async def send_message(self, uid, text, **kw):
            raise RuntimeError("bot was blocked by the user")

    class Cfg:
        allowlist_user_ids = frozenset({1, 2})

    class DB:
        def has_started(self, uid):
            return True

    class S:
        config = Cfg()
        db = DB()

    class Ctx:
        bot = Bot()

    await notify_team(Ctx(), S(), actor_id=1, text="hi")   # must not raise
