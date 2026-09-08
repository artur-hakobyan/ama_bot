"""Answering the proposals: typed feedback, and steering a single replacement.

The reviewer wrote "please vary more with the topics ... can you think about
more topic ideas?" under the proposals and nothing happened: no step was set, so
the router dropped the message. Replace #N regenerated blindly and returned a
near-identical proposal, because nothing told the model what was wrong.
"""
import pytest

from bot.claude_client import PROPOSALS_SCHEMA


def test_propose_articles_passes_the_reviewers_words(monkeypatch):
    """Guidance must reach the prompt verbatim and outrank the generic rules."""
    import asyncio

    from bot import claude_client

    captured = {}

    class FakeClaude:
        async def _ask(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"proposals": []}'

        def _parse_json(self, raw):
            import json
            return json.loads(raw)

    asyncio.run(claude_client.propose_articles(
        FakeClaude(), [], "Grundlagen",
        guidance="vary the topics more, don't explain the basics every time",
        avoid=["Akustikbilder: Was sie sind"]))

    assert "vary the topics more" in captured["prompt"]
    assert "Vorrang" in captured["prompt"], "guidance must outrank the defaults"
    # A rejected title must be named, or the retry returns it in new words.
    assert "Akustikbilder: Was sie sind" in captured["prompt"]


def test_without_guidance_no_empty_blocks_are_added():
    import asyncio

    from bot import claude_client

    captured = {}

    class FakeClaude:
        async def _ask(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"proposals": []}'

        def _parse_json(self, raw):
            import json
            return json.loads(raw)

    asyncio.run(claude_client.propose_articles(FakeClaude(), [], "Grundlagen"))
    assert "ANWEISUNG DES REDAKTEURS" not in captured["prompt"]
    assert "abgelehnt" not in captured["prompt"]


def test_variety_is_demanded_by_default():
    """Three proposals that all explain the basics is the failure to avoid."""
    import asyncio

    from bot import claude_client

    captured = {}

    class FakeClaude:
        async def _ask(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"proposals": []}'

        def _parse_json(self, raw):
            import json
            return json.loads(raw)

    asyncio.run(claude_client.propose_articles(FakeClaude(), [], "Grundlagen"))
    assert "Vielfalt" in captured["prompt"]


def test_proposals_text_invites_a_typed_reply():
    from bot.modules.blog import proposals_text

    text = proposals_text(
        [{"title": "T", "keyword": "k", "value": "v", "outline": ["a"]}],
        "Grundlagen")
    assert "write what you want changed" in text


def test_the_schema_still_requires_every_field():
    assert set(PROPOSALS_SCHEMA["properties"]["proposals"]["items"]["required"]) == {
        "keyword", "title", "outline", "supporting_keywords", "value"}


def test_a_typed_reply_reaches_the_batch_handler(tmp_path):
    """The bug: no step was set, so the router silently dropped the message."""
    from bot.db import Database
    from bot.modules.blog import _await_batch_feedback

    class S:
        db = Database(str(tmp_path / "t.db"))

    services = S()
    batch_id = services.db.create_batch(7, "Grundlagen", [{"keyword": "k"}])
    _await_batch_feedback(services, 7, batch_id)

    session = services.db.get_session(7)
    assert session["step"] == "blog:batchfeedback"
    assert session["context"]["batch_id"] == batch_id
    # The router dispatches on the "blog:" prefix.
    assert session["step"].split(":", 1)[0] == "blog"
    services.db.close()


def test_approval_clears_the_feedback_step(tmp_path):
    """After approving, a typed message belongs to the article, not a re-proposal."""
    from bot.db import Database
    from bot.modules.blog import _await_batch_feedback

    db = Database(str(tmp_path / "t.db"))
    class S:
        pass
    services = S()
    services.db = db
    batch_id = db.create_batch(7, "Grundlagen", [{"keyword": "k"}])
    _await_batch_feedback(services, 7, batch_id)
    db.set_step(7, None, {})            # what bstart does
    assert db.get_session(7)["step"] is None
    db.close()
