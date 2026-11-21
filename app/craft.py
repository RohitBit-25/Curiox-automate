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


# Short-text tells (comments, DMs). The detect.py panel needs ~25+ words; these work on one line.
_SLOP_WORDS = [(e["find"], re.compile(rf"\b{re.escape(e['find'])}\b", re.I)) for e in SLOP["words"] + SLOP["phrases"]]
_STRUCTURES = [(e["name"], re.compile(e["regex"])) for e in SLOP["structures"]]  # regexes carry their own (?i)
_OPENER = re.compile(r"^\W*(great (point|post|insight|share|question)|love (this|how|that|it)|absolutely|totally agree|so true|"
                     r"interesting|well said|spot on|couldn'?t agree|this!|wow|kudos|hats off|what a|amazing|brilliant|"
                     r"fantastic|excellent|such a|really (insightful|helpful|great))\b", re.I)
_ASSISTANT = re.compile(r"thanks? (you )?for reaching out|happy to help|i'?d be (happy|glad) to|feel free to|don'?t hesitate|"
                        r"hope (this|that) helps|let me know if you have any|great question|i understand your|"
                        r"any (other|further) questions|looking forward to hearing|hi there|"
                        r"what do you think\??\s*$|thoughts\?\s*$|curious to (hear|know)|would love to (hear|know)|"
                        r"at the end of the day|that'?s the real (lesson|win)|this is the way|game.?changer", re.I)
_LOUD_EMOJI = re.compile("[\U0001F680\U0001F525\u2728\U0001F4A1\U0001F4AF]")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def slop_hits(text: str) -> list[str]:
    """Phrases that make a short comment or DM read machine-written. Empty list = reads human."""
    hits = [f"opener: {m.group(0).strip()}" for m in [_OPENER.match(text)] if m]
    hits += [f"phrase: {m.group(0)}" for m in _ASSISTANT.finditer(text)]
    hits += [f"word: {w}" for w, rx in _SLOP_WORDS if rx.search(text)]
    hits += [f"shape: {name}" for name, rx in _STRUCTURES if rx.search(text)]
    if re.search("[\u2014\u2013]", text):
        hits.append("dash: em/en dash")
    if ";" in text:
        hits.append("semicolon")
    if text.count("!") > 1:
        hits.append("too many !")
    if _LOUD_EMOJI.search(text) or len(_EMOJI.findall(text)) > 1:
        hits.append("emoji")
    if "#" in text and re.search(r"(^|\s)#\w", text):
        hits.append("hashtag")
    return list(dict.fromkeys(hits))


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
