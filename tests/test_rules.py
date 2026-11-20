from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.rules import (
    detect_language, fingerprint, is_near_duplicate, quality_issues, recency_score, relevance_score,
)


@pytest.mark.parametrize("text,lang", [
    ("This RAG pipeline is clean", "en"),
    ("यह बहुत अच्छा तरीका है", "hi"),
    ("bhai ye setup kya mast hai", "hinglish"),
    ("🔥🔥", "en"),
])
def test_detect_language(text, lang):
    assert detect_language(text) == lang


def test_relevance_score():
    w = settings.RELEVANCE_WEIGHTS
    ones = {"topic": 1, "audience": 1, "conversation": 1, "quality": 1}
    assert relevance_score(ones, 1.0, w) == 1.0
    assert relevance_score({}, 0.0, w) == 0.0
    # clamps out-of-range LLM numbers
    assert relevance_score({k: 5 for k in ones}, 1.0, w) == 1.0
    # topic+audience+conversation perfect, rest zero = 0.75 -> above 0.65 threshold
    assert relevance_score({"topic": 1, "audience": 1, "conversation": 1}, 0.0, w) == 0.75
    # weights are normalized
    assert relevance_score(ones, 1.0, {k: v * 10 for k, v in w.items()}) == 1.0


def test_recency_score():
    now = datetime.now(UTC)
    assert recency_score(now, now) == 1.0
    assert recency_score(now - timedelta(hours=48), now) == pytest.approx(0.5)
    assert recency_score(None, now) == 0.5
    assert recency_score((now - timedelta(hours=48)).replace(tzinfo=None), now) == pytest.approx(0.5)


def test_fingerprint_and_near_duplicate():
    assert fingerprint("Great point!!") == fingerprint("great point 🔥")
    prev = ["How are you handling tool permissions as connected services grow?"]
    assert is_near_duplicate("How are you handling tool permissions as connected services scale?", prev)
    assert not is_near_duplicate("Backtesting with survivorship-free data changes everything.", prev)


def test_quality_issues():
    assert "follow_solicitation" in quality_issues("Love it, follow me for more", "en", [], [], "insight")
    assert "unverified_experience" in quality_issues(
        "We built a similar Celery retry layer and it worked well.", "en", [], [], "experience")
    assert "unverified_experience" not in quality_issues(
        "On TradeLens we built a similar Celery retry layer.", "en", [], ["TradeLens"], "experience")
    assert "generic_praise" in quality_issues("Nice post!", "en", [], [], "appreciation")
    assert "promotional" in quality_issues("Curiox does this better, use code X", "en", [], [], "insight")
    assert "language_mismatch" in quality_issues("This is a clean approach to retries.", "hi", [], [], "insight")
    assert quality_issues("", "en", [], [], "insight") == ["empty"]
    assert quality_issues(
        "Idempotent tasks plus a dead-letter queue made retries boring for us in the best way.",
        "en", [], [], "insight") == []
