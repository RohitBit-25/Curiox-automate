"""Comments and DMs that read machine-written get one rewrite pass, and stay flagged (never auto-sent) if still off."""
import pytest
from sqlalchemy import select

from app import craft, inbox, pipeline
from app.models import CommentSuggestion
from app.prefs import SettingsPatch, save_settings
from tests.conftest import FakeAdapter, FakeLLM
from tests.test_flow import ANALYSIS, GENERATION, SUGGESTIONS, submit
from tests.test_inbox import DM, seed_conv

SLOP = "Great insight! Retries are a game-changer — robust, seamless and reliable. Thoughts?"


@pytest.mark.parametrize("text", [
    SLOP, "Hi there! Thanks for reaching out. I'd be happy to help.", "It's not just a tool, it's a mindset.",
    "Love this 🚀🔥", "Absolutely agree; consistency matters.", "Hope this helps, let me know if you have any questions.",
])
def test_slop_flagged(text):
    assert craft.slop_hits(text)


@pytest.mark.parametrize("text", [
    "did you hit the visibility_timeout thing with Redis? long tasks got picked up twice",
    "register ki photo upload karne ka option de do, shift aasaan ho jaata hai",
    "Same here 🙏 took me a week to get the webhook signature right",
    "hey! depends on what you need. just info + contact form, or fees and attendance too?",
    *[s["text"] for s in SUGGESTIONS],
])
def test_human_text_passes(text):
    assert craft.slop_hits(text) == []


async def test_comment_rewritten_then_flagged_if_still_slop(client, session):
    post_id = (await submit(client)).json()["id"]
    gen = {**GENERATION, "comment_suggestions": [{"text": SLOP, "strategy": "question"}, SUGGESTIONS[1]]}
    fixed = "do you set acks_late on these? otherwise a worker crash mid-task just drops the retry"
    llm = FakeLLM(ANALYSIS, gen, {"texts": [fixed]})
    await pipeline.process_post(session, post_id, llm)
    assert "game-changer" in llm.calls[-1] and len(llm.calls) == 3  # only the sloppy draft went to the rewrite
    rows = list(await session.scalars(select(CommentSuggestion).order_by(CommentSuggestion.position)))
    assert rows[0].text == fixed and "ai_slop" not in rows[0].quality_flags

    post2 = (await submit(client, "XYZ987abc")).json()["id"]
    await pipeline.process_post(session, post2, FakeLLM(ANALYSIS, {**gen, "comment_suggestions": [
        {"text": SLOP, "strategy": "question"}]}, {"texts": [SLOP]}))
    row = await session.scalar(select(CommentSuggestion).where(CommentSuggestion.post_id == post2))
    assert "ai_slop" in row.quality_flags  # kept for the human, never auto-approved


async def test_dm_rewritten_and_never_auto_sent_if_still_slop(session, monkeypatch):
    monkeypatch.setattr(inbox.workers, "enqueue_send_dm", lambda _: pytest.fail("must not auto-send"))
    await save_settings(session, "tester", SettingsPatch(mode_dms="auto"))
    conv = await seed_conv(session)
    bot = "Hi there! Thanks for reaching out. Yes, it works with Celery 5."
    draft = await inbox.draft_dm_reply(session, conv.id, FakeLLM({**DM, "reply": bot}, {"texts": [bot]}),
                                       adapter_factory=FakeAdapter())
    assert draft.status == "draft" and draft.meta["blocked"] == "quality_flags"

    queued = []
    monkeypatch.setattr(inbox.workers, "enqueue_send_dm", queued.append)
    good = "yep, works with celery 5. retries back off on their own"
    draft = await inbox.draft_dm_reply(session, conv.id, FakeLLM({**DM, "reply": bot}, {"texts": [good]}),
                                       adapter_factory=FakeAdapter())
    assert draft.text == good and draft.status == "approved" and queued == [draft.id]


async def test_learns_from_what_you_write(session):
    from app.models import Approval, CommentSuggestion, Message, Post
    from app.prefs import voice_block, voice_samples, voice_with_samples
    conv = await seed_conv(session)
    session.add_all([
        Message(conversation_id=conv.id, direction="out", text="haan bhai kal tak bhej dunga", status="sent", actor="instagram_app"),
        Message(conversation_id=conv.id, direction="out", text="AI draft sent as is", status="sent", actor="me",
                meta={"human_written": False}),
        Message(conversation_id=conv.id, direction="out", text="edited by me", status="sent", actor="me",
                meta={"human_written": True}),
        Message(conversation_id=conv.id, direction="out", text="auto reply", status="sent", actor="auto"),
        Post(id=50, platform="instagram", source="manual_link", external_id="P50", text="x"),
        CommentSuggestion(id=7, post_id=50, position=0, text="mine now", strategy="insight", edited=True),
        Approval(post_id=50, suggestion_id=7, decision="approved", final_text="mine now", actor="me"),
    ])
    await session.commit()
    assert set(await voice_samples(session)) == {"haan bhai kal tak bhej dunga", "edited by me", "mine now"}
    assert "haan bhai kal tak bhej dunga" in voice_block(await voice_with_samples(session))
