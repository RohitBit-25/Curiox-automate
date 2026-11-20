from app import craft


def test_hook_score_and_classify():
    weak = craft.hook_score("Hey guys, in this video I'll show you my setup")
    assert weak["verdict"] == "WEAK" and weak["flags"]
    strong = craft.hook_score("$18,000 is what one missing clause cost me.")
    assert strong["score"] >= 70
    assert craft.classify_hook("$18,000 is what one missing clause cost me.")["name"] == "Cost Confession"
    assert "Cost Confession" in craft.hooks_for_prompt()


def test_ai_tells():
    slop = ("In today's fast-paced world, we leverage robust, seamless solutions to unlock the full potential "
            "of your business. It's not just a tool, it's a game-changer that empowers teams to elevate every journey.")
    plain = ("I rebuilt our invoice bot last Tuesday. It used to double-charge two clients a week because the "
             "Stripe webhook fired twice. One idempotency key fixed it. Took 40 minutes, I'm still annoyed it took me a year.")
    assert craft.ai_tells(slop)["human_score"] < craft.ai_tells(plain)["human_score"]


def test_caption_lint():
    tags = ["a", "b", "c", "d", "e", "f"]
    lint = craft.caption_lint("I cut deploy time from 40 min to 6.", tags)
    assert lint["verdict"] == "FIX"
    assert next(c for c in lint["checks"] if c["check"] == "HASHTAGS")["status"] == "FAIL"
    first = craft.caption_lint("#ai is everywhere")
    assert next(c for c in first["checks"] if c["check"] == "FIRST LINE")["status"] == "FAIL"


def test_hashtag_issues():
    issues = craft.hashtag_issues([{"tag": "ai", "tier": "broad"}, {"tag": "tech", "tier": "broad"}])
    assert {"too_many_broad", "too_few_niche", "count"} <= set(issues)
    assert "filler:#viral" in craft.hashtag_issues([{"tag": "viral", "tier": "broad"}])
    assert craft.hashtag_issues([{"tag": "celerytasks", "tier": "niche"}, {"tag": "pythondevs", "tier": "niche"},
                                 {"tag": "automation", "tier": "mid"}]) == []


def test_rubric_and_scrub():
    assert craft.rubric_total([{"id": "name_field", "score": 99}, {"id": "bogus", "score": 5}]) == 12
    assert craft.scrub("hi​there “ok” — yes") == 'hithere "ok" — yes'
    assert "name_field (12)" in craft.rubric_for_prompt()
