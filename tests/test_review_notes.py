"""Reviewing in the Google Doc: comments and highlights drive the rewrite.

Commenting on the passage itself is more natural than retyping the objection in
Telegram, and the comment carries the exact text it refers to.
"""
import pytest

from bot.google_drive import GoogleDocs, _unescape


class FakeDrive:
    def __init__(self, comments=None, html="", fail=False):
        self._comments = {"comments": comments or []}
        self._html = html
        self._fail = fail

    # --- drive.comments() ---
    def comments(self):
        return self

    def list(self, **kw):
        return self._Exec(self._comments, self._fail)

    # --- drive.files() ---
    def files(self):
        return self

    def export(self, **kw):
        return self._Exec(self._html.encode("utf-8"), self._fail)

    class _Exec:
        def __init__(self, value, fail):
            self._value, self._fail = value, fail

        def execute(self):
            if self._fail:
                raise RuntimeError("Drive unavailable")
            return self._value


def _docs(drive):
    return GoogleDocs(type("G", (), {"_drive": drive})(), "folder")


def test_a_comment_carries_the_passage_it_sits_on():
    drive = FakeDrive(comments=[{
        "content": "Dieser Abschnitt passt nicht rein.",
        "quotedFileContent": {"value": "Melamin-Schaum: Leicht und weit verbreitet"},
    }])
    notes = _docs(drive).review_notes("f")
    assert notes[0]["note"] == "Dieser Abschnitt passt nicht rein."
    assert "Melamin-Schaum" in notes[0]["quote"]
    assert notes[0]["kind"] == "comment"


def test_resolved_comments_are_skipped():
    """Re-applying a resolved comment would undo the fix it asked for."""
    drive = FakeDrive(comments=[
        {"content": "already handled", "resolved": True},
        {"content": "still open"},
    ])
    notes = _docs(drive).review_notes("f")
    assert [n["note"] for n in notes] == ["still open"]


def test_replies_are_part_of_the_instruction():
    drive = FakeDrive(comments=[{
        "content": "Kürzen.",
        "replies": [{"content": "Und den letzten Satz streichen."}],
    }])
    assert "letzten Satz" in _docs(drive).review_notes("f")[0]["note"]


def test_entities_in_quoted_text_are_decoded():
    """Drive returns &#228; for ä; the model must see the real passage."""
    drive = FakeDrive(comments=[{
        "content": "x", "quotedFileContent": {"value": "G&#228;ngige Materialien"}}])
    assert _docs(drive).review_notes("f")[0]["quote"] == "Gängige Materialien"
    assert _unescape("Gr&#246;&#223;e &amp; Form") == "Größe & Form"


def test_a_highlight_is_read_as_its_own_kind():
    """A highlight says "this is wrong" without saying why."""
    html = ('<style>.c7{background-color:#ffff00;font-weight:400}</style>'
            '<body><p><span class="c7">Dieser Satz stimmt nicht.</span></p></body>')
    notes = _docs(FakeDrive(html=html)).review_notes("f")
    highlights = [n for n in notes if n["kind"] == "highlight"]
    assert highlights and highlights[0]["quote"] == "Dieser Satz stimmt nicht."
    assert highlights[0]["note"] == ""


def test_the_page_background_is_not_a_highlight():
    """Every Google export sets background-color:#ffffff on the page."""
    html = ('<style>.c1{background-color:#ffffff}</style>'
            '<body><p><span class="c1">Normaler Text.</span></p></body>')
    assert _docs(FakeDrive(html=html)).review_notes("f") == []


def test_an_empty_comment_is_ignored():
    drive = FakeDrive(comments=[{"content": "   "}])
    assert _docs(drive).review_notes("f") == []


def test_a_drive_failure_returns_nothing_rather_than_raising():
    """A Drive hiccup must not break the review; the draft is still there."""
    assert _docs(FakeDrive(fail=True)).review_notes("f") == []
