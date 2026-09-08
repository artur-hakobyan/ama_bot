"""The operator's brief is read from Drive, not transcribed into the code.

The .docx was copied into the prompts by hand once. Everything the operator
added afterwards was invisible, and the file looked like it was still in use —
so the failure was silent in the worst way.
"""
import zipfile
import io

import pytest

from bot.google_drive import LivePrompt, _docx_text


def _docx_bytes(paragraphs) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document><w:body>{body}'
                   f'</w:body></w:document>')
    return buf.getvalue()


class FakeDrive:
    def __init__(self, mime, payload, fail=False):
        self._mime = mime
        self._payload = payload
        self._fail = fail
        self.fetches = 0

    def files(self):
        return self

    def get(self, fileId=None, fields=None):
        return self._Exec({"mimeType": self._mime}, self)

    def export(self, fileId=None, mimeType=None):
        return self._Exec(self._payload, self, count=True)

    def get_media(self, fileId=None):
        return self._Exec(self._payload, self, count=True)

    class _Exec:
        def __init__(self, value, parent, count=False):
            self._value, self._parent, self._count = value, parent, count

        def execute(self):
            if self._parent._fail:
                raise RuntimeError("Drive unavailable")
            if self._count:
                self._parent.fetches += 1
            return self._value


def _client(drive):
    return type("G", (), {"_drive": drive})()


def test_a_google_doc_is_read_as_text():
    drive = FakeDrive("application/vnd.google-apps.document", b"live text")
    lp = LivePrompt(_client(drive), {"workflow": "id1"})
    assert lp.text("workflow") == "live text"


def test_a_docx_is_parsed_without_a_document_library():
    """The brief is a .docx; a zip must not be handed to the model as bytes."""
    blob = _docx_bytes(["Schreibstil:", "Duze den Leser."])
    drive = FakeDrive("application/vnd.openxmlformats-officedocument."
                      "wordprocessingml.document", blob)
    lp = LivePrompt(_client(drive), {"prompt": "id2"})
    text = lp.text("prompt")
    assert "Schreibstil:" in text and "Duze den Leser." in text


def test_docx_entities_are_unescaped():
    assert "Größe & Form" in _docx_text(_docx_bytes(["Größe &amp; Form"]))


def test_the_document_is_cached_across_a_batch():
    """Three articles must not re-download the brief for every pass."""
    drive = FakeDrive("application/vnd.google-apps.document", b"text")
    lp = LivePrompt(_client(drive), {"workflow": "id1"})
    for _ in range(5):
        lp.text("workflow")
    assert drive.fetches == 1


def test_a_drive_failure_never_stops_an_article():
    """The code's own rules still apply; they are just not topped up."""
    drive = FakeDrive("application/vnd.google-apps.document", b"x", fail=True)
    lp = LivePrompt(_client(drive), {"workflow": "id1"})
    assert lp.text("workflow") == ""


def test_a_stale_copy_is_preferred_to_nothing():
    drive = FakeDrive("application/vnd.google-apps.document", b"good text")
    lp = LivePrompt(_client(drive), {"workflow": "id1"})
    assert lp.text("workflow") == "good text"
    lp._cache["workflow"] = (0, "good text")   # expire it
    drive._fail = True
    assert lp.text("workflow") == "good text"


def test_an_unconfigured_document_is_simply_empty():
    lp = LivePrompt(_client(FakeDrive("x", b"")), {"workflow": ""})
    assert lp.text("workflow") == ""
    assert lp.text("never-configured") == ""
