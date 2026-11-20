"""Pure, deterministic rules: language hint, scoring, dedupe, quality checks."""
import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher

# ponytail: word-list heuristic; the LLM's detected_language is authoritative, this is a hint + mismatch check
HINGLISH = {
    "hai", "hain", "kya", "nahi", "nhi", "bhai", "yaar", "acha", "accha", "bahut", "bohot", "kaise",
    "kyun", "kyu", "matlab", "sahi", "mast", "bilkul", "toh", "tha", "thi", "raha", "rahe", "karna",
    "karo", "mein", "hum", "aap", "tum", "yeh", "ye", "woh", "kuch", "sab", "abhi", "bas", "lekin",
    "bhi", "haan", "samajh", "dekho", "chalo", "kaam", "waala", "wala", "zyada", "kab",
}


def detect_language(text: str) -> str:
    """Returns "hi" | "hinglish" | "en"."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    if sum("ऀ" <= c <= "ॿ" for c in letters) / len(letters) > 0.3:
        return "hi"
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(w in HINGLISH for w in words)
    return "hinglish" if words and hits >= 2 and hits / len(words) >= 0.15 else "en"


def recency_score(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 0.5
    if published_at.tzinfo is None:  # SQLite returns naive datetimes
        published_at = published_at.replace(tzinfo=UTC)
    age_h = max(0.0, (now - published_at).total_seconds() / 3600)
    return 0.5 ** (age_h / 48)  # 48h half-life


def relevance_score(scores: dict, recency: float, weights: dict) -> float:
    parts = {k: min(1.0, max(0.0, float(scores.get(k, 0)))) for k in ("topic", "audience", "conversation", "quality")}
    parts["recency"] = recency
    return round(sum(weights[k] * parts[k] for k in weights) / sum(weights.values()), 4)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    t = "".join(c for c in t if unicodedata.category(c)[0] not in "PS")  # drop punctuation, symbols, emoji
    return " ".join(t.split())


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def is_near_duplicate(text: str, previous: list[str], ratio: float = 0.85) -> bool:
    # ponytail: linear SequenceMatcher scan over recent texts; MinHash if history grows large
    n = normalize(text)
    return any(n == (p := normalize(prev)) or SequenceMatcher(None, n, p).ratio() >= ratio for prev in previous)


FOLLOW_RE = re.compile(
    r"\b(follow (me|us|back)|check (out )?(my|our) (profile|page|bio)|link in (my |our )?bio|dm (me|us)|f4f|subscribe)\b", re.I)
PROMO_RE = re.compile(r"\b(buy now|discount|promo code|use code|sign up|limited offer|curiox)\b", re.I)
PRAISE_RE = re.compile(
    r"\b(great|nice|awesome|amazing|love (this|it)|so true|well said|informative|helpful|insightful|superb|mast|badhiya)\b", re.I)
CLAIM_RE = re.compile(r"\b(we built|i built|we use|we used|at curiox|in our (project|stack)|we shipped)\b", re.I)


def quality_issues(text: str, post_language: str | None, previous: list[str],
                   verified_projects: list[str], strategy: str) -> list[str]:
    if not text.strip():
        return ["empty"]
    issues = []
    if len(text) > 400:
        issues.append("too_long")
    if is_near_duplicate(text, previous):
        issues.append("duplicate")
    if FOLLOW_RE.search(text):
        issues.append("follow_solicitation")
    if PROMO_RE.search(text) and strategy != "experience":
        issues.append("promotional")
    if len(text.split()) < 7 and PRAISE_RE.search(text):
        issues.append("generic_praise")
    mentions_project = any(p.lower() in text.lower() for p in verified_projects)
    if (strategy == "experience" or CLAIM_RE.search(text)) and not mentions_project:
        issues.append("unverified_experience")
    got = detect_language(text)
    if (post_language == "hi" and got == "en") or (post_language == "en" and got == "hi"):
        issues.append("language_mismatch")
    return issues


DROP = {"empty", "too_long", "duplicate", "follow_solicitation", "promotional", "unverified_experience"}  # pipeline drops
HARD = {"empty", "too_long", "duplicate", "follow_solicitation"}  # approve endpoint blocks
