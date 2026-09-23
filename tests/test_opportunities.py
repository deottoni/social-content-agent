from datetime import timedelta

import pytest

from conftest import write_config
from instagram_os.models import InvalidTransition, OpportunityStatus as O
from instagram_os.opportunities import (OpportunityMonitor, activity_signal, priority_for, recency_signal,
                                        score_signals)
from instagram_os.reasoner import OpportunityAssessment, Reasoner, RulesReasoner
from instagram_os.store import iso, utcnow


class ScriptedOpp(Reasoner):
    name = "scripted"

    def __init__(self, by_caption):
        self.by_caption = by_caption

    def assess_opportunity(self, post, context, topics):
        rel, action, comment = self.by_caption.get(post["caption"], (0.0, "skip", ""))
        return OpportunityAssessment(summary=post["caption"][:60], topical_relevance=rel, audience_overlap=rel,
                                     brand_value=rel, comment_confidence=rel, why="overlaps AI for SMBs",
                                     opportunity_type="question_to_answer", suggested_action=action,
                                     suggested_comment=comment)


def post(pid, caption, comments=10, hours_ago=1):
    return {"id": pid, "caption": caption, "comments_count": comments, "like_count": 50,
            "permalink": f"https://www.instagram.com/p/{pid}/", "media_type": "IMAGE",
            "timestamp": iso(utcnow() - timedelta(hours=hours_ago))}


@pytest.fixture
def fb_fake(fake):
    fake.login_type = "facebook"
    return fake


def test_scoring_and_priority_math(brand):
    w = brand.config["opportunities"]["weights"]
    assert score_signals({k: 1.0 for k in w}, w) == 1.0
    assert score_signals({k: 0.0 for k in w}, w) == 0.0
    assert priority_for(0.8, {"high": 0.7, "medium": 0.45}) == "HIGH"
    assert priority_for(0.5, {"high": 0.7, "medium": 0.45}) == "MEDIUM"
    assert priority_for(0.1, {"high": 0.7, "medium": 0.45}) == "LOW"
    assert activity_signal(0) == 0 and activity_signal(100) == 1.0
    assert recency_signal(utcnow(), 48) > 0.99 and recency_signal(utcnow() - timedelta(hours=60), 48) == 0


def test_watchlist_accounts_become_ranked_human_opportunities(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"accounts": ["acme"], "topics": ["AI"]},
                                       "relationships": {"acme": 1.0}})
    fb_fake.discovery["acme"] = {"username": "acme", "media": {"data": [
        post("p1", "What's the biggest AI mistake small businesses make?", comments=80),
        post("p2", "Our new office dog", comments=5),
        post("p3", "Is automation worth it for a 3-person team?", comments=2, hours_ago=40)]}}
    reasoner = ScriptedOpp({
        "What's the biggest AI mistake small businesses make?": (0.95, "comment", "Treating it as a tool rollout instead of a workflow change."),
        "Is automation worth it for a 3-person team?": (0.6, "comment", ""),
    })
    result = OpportunityMonitor(brand, store, fb_fake, activity, reasoner).run()
    assert result["created"] == 2
    opps = store.opportunities()
    top = opps[0]
    assert top["source_account"] == "acme" and top["source_media_id"] == "p1" and top["priority"] == "HIGH"
    assert top["source_url"].startswith("https://www.instagram.com/p/")
    assert top["suggested_comment"] and top["status"] == "NEW" and top["expires_at"]
    assert opps[1]["score"] < top["score"]
    # nothing was posted anywhere: the API client has no comment-on-others method at all
    assert not hasattr(fb_fake, "comment_on_media")


def test_rerun_does_not_duplicate(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"accounts": ["acme"]}})
    fb_fake.discovery["acme"] = {"username": "acme", "media": {"data": [post("p1", "q?")]}}
    m = OpportunityMonitor(brand, store, fb_fake, activity, ScriptedOpp({"q?": (0.9, "comment", "")}))
    m.run()
    m.run()
    assert len(store.opportunities()) == 1


def test_min_score_and_max_per_run_prevent_volume(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"accounts": ["acme"]}, "max_new_per_run": 2})
    fb_fake.discovery["acme"] = {"username": "acme", "media": {"data": [post(f"p{i}", f"c{i}") for i in range(6)]}}
    scripted = {f"c{i}": (0.9, "comment", "") for i in range(5)}
    scripted["c5"] = (0.05, "comment", "")
    OpportunityMonitor(brand, store, fb_fake, activity, ScriptedOpp(scripted)).run()
    assert len(store.opportunities()) == 2


def test_hashtag_budget_respected(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"hashtags": ["aiagents"]}})
    for i in range(30):
        store.record_hashtag_search(f"tag{i}", f"h{i}")
    fb_fake.hashtags["aiagents"] = ("hid", [post("p1", "x")])
    OpportunityMonitor(brand, store, fb_fake, activity, ScriptedOpp({"x": (0.9, "comment", "")})).run()
    assert not any(c[0] == "hashtag_search" for c in fb_fake.calls)


def test_hashtag_id_is_cached(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"hashtags": ["aiagents"]}})
    fb_fake.hashtags["aiagents"] = ("hid", [post("p1", "x")])
    m = OpportunityMonitor(brand, store, fb_fake, activity, ScriptedOpp({"x": (0.9, "comment", "")}))
    m.run()
    m.run()
    assert sum(1 for c in fb_fake.calls if c[0] == "hashtag_search") == 1
    assert store.opportunities()[0]["source_account"] is None  # hashtag search can't return authors


def test_instagram_login_degrades_to_manual(brand, store, fake, activity):
    write_config(brand, opportunities={"watchlist": {"accounts": ["acme"], "topics": ["AI"]}})
    m = OpportunityMonitor(brand, store, fake, activity, RulesReasoner())
    assert m.run()["created"] == 0
    assert "unavailable" in (brand.instagram_dir / "activity.log").read_text()
    opp_id = m.add_manual("https://www.instagram.com/p/ABC123/", "How are you using AI in your shop?", account="@acme")
    assert store.get_opportunity(opp_id)["source_media_id"] == "manual-ABC123"


def test_status_workflow_and_expiry(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"accounts": ["acme"]}})
    fb_fake.discovery["acme"] = {"username": "acme", "media": {"data": [post("p1", "a"), post("p2", "b")]}}
    m = OpportunityMonitor(brand, store, fb_fake, activity, ScriptedOpp({"a": (0.9, "comment", ""), "b": (0.9, "comment", "")}))
    m.run()
    a, b = store.opportunities()
    store.transition_opportunity(a["id"], O.REVIEWED)
    store.transition_opportunity(a["id"], O.APPROVED)
    store.transition_opportunity(a["id"], O.COMPLETED, "commented by hand")
    with pytest.raises(InvalidTransition):
        store.transition_opportunity(a["id"], O.NEW)
    store.conn.execute("UPDATE opportunities SET expires_at=? WHERE id=?", (iso(utcnow() - timedelta(hours=1)), b["id"]))
    assert m.expire() == 1
    assert store.get_opportunity(b["id"])["status"] == "EXPIRED"


def test_unsafe_suggested_comment_is_dropped(brand, store, fb_fake, activity):
    write_config(brand, opportunities={"watchlist": {"accounts": ["acme"]}})
    fb_fake.discovery["acme"] = {"username": "acme", "media": {"data": [post("p1", "a")]}}
    OpportunityMonitor(brand, store, fb_fake, activity,
                       ScriptedOpp({"a": (0.9, "comment", "Check out https://our.site for 20% off")})).run()
    opp = store.opportunities()[0]
    assert opp["suggested_comment"] is None and "dropped" in opp["notes"]


def test_third_party_comment_is_locked_to_human(brand):
    write_config(brand, autonomy={"third_party_comment": "auto"})
    assert brand.autonomy("third_party_comment") == "human"
