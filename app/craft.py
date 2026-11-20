"""Content-craft checks: thin wrappers over the vendored instagram-agent-skill tools (MIT, see NOTICE)."""
import json
import re
from pathlib import Path

from app.vendor.ig_agent import caption as _caption
from app.vendor.ig_agent import detect as _detect
from app.vendor.ig_agent import hookscore as _hook

_DATA = Path(_hook.__file__).parent
HOOKS = json.loads((_DATA / "hooks.json").read_text())
SLOP = json.loads((_DATA / "slop.json").read_text())
RUBRIC = json.loads((_DATA / "rubric.json").read_text())


def _compile(pattern: str):
    try:
        return re.compile(pattern, re.I)
    except re.error:
        return None


_BY_ID = {h["id"]: h for h in HOOKS["hooks"]}
_CLASSIFY = [(h, rx) for i in HOOKS["classify_order"] if (h := _BY_ID.get(i)) and (rx := _compile(h.get("match", "")))]
def _codepoints(spec: str) -> range:  # "U+200B" or "U+E0000-U+E007F"
    lo, _, hi = spec.partition("-")
    return range(int(lo[2:], 16), int((hi or lo)[2:], 16) + 1)


_INVISIBLE = {chr(c) for e in SLOP["invisible"] if e.get("action") == "delete" for c in _codepoints(e["cp"])}
_TYPO = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", " ": " "})
FILLER = {t.lstrip("#") for t in _caption.FILLER_TAGS}


def hook_score(text: str) -> dict:
    results, overall, verdict, flags = _hook.run(text)
    return {
        "score": round(overall, 1), "verdict": verdict, "flags": flags,
        "checks": {k: {"score": round(v[0], 1), "detail": v[1]} for k, v in results.items()},
        "weakest": min(_hook.CHECKS, key=lambda c: results[c][0]),
    }


def classify_hook(text: str) -> dict | None:
    return next(({"id": h["id"], "name": h["name"]} for h, rx in _CLASSIFY if rx.search(text)), None)


def hooks_for_prompt() -> str:
    return "\n".join(f"{h['id']}. {h['name']}: {h['template']}" for h in HOOKS["hooks"])


def ai_tells(text: str) -> dict:
    """How human the text reads. verdict: PASS | REVIEW | FLAGGED."""
    results, overall, verdict = _detect.run(text, SLOP)
    return {"human_score": round(overall, 1), "verdict": verdict,
            "checks": {k: {"score": round(v[0], 1), "detail": v[1]} for k, v in results.items()}}


def scrub(text: str) -> str:
    """Remove invisible watermark characters and normalise typography. Keeps em dashes."""
    return "".join(c for c in text if c not in _INVISIBLE).translate(_TYPO)


def compose_caption(caption: str, tags: list[str]) -> str:
    return caption.strip() + ("\n.\n" + " ".join("#" + t.lstrip("#") for t in tags) if tags else "")


def caption_lint(caption: str, hashtags: list[str] = ()) -> dict:
    a = _caption.analyse(compose_caption(caption, list(hashtags)))
    return {k: a[k] for k in ("visible", "truncated", "characters", "checks", "verdict")}


def hashtag_issues(tags: list[dict]) -> list[str]:
    """tags: [{"tag", "tier": niche|mid|broad}]."""
    issues, seen = [], set()
    if not 3 <= len(tags) <= 5:
        issues.append("count")
    tiers = [t.get("tier") for t in tags]
    if tiers.count("broad") > 1:
        issues.append("too_many_broad")
    if tiers.count("niche") < 2:
        issues.append("too_few_niche")
    for t in tags:
        tag = t.get("tag", "").lstrip("#")
        if not re.fullmatch(r"[A-Za-z0-9_]+", tag):
            issues.append(f"invalid:#{tag}")
        elif tag.lower() in FILLER:
            issues.append(f"filler:#{tag}")
        if tag.lower() in seen:
            issues.append(f"duplicate:#{tag}")
        seen.add(tag.lower())
    return issues


def rubric_total(scores: list[dict]) -> int:
    points = {i["id"]: i["points"] for i in RUBRIC["items"]}
    return round(sum(min(max(float(s.get("score", 0)), 0), points[s["id"]])
                     for s in scores if s.get("id") in points))


def rubric_for_prompt() -> str:
    return "\n".join(f"- {i['id']} ({i['points']}): {i['full_marks']} | common fail: {i['common_fail']}"
                     for i in RUBRIC["items"])
