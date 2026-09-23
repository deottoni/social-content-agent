from datetime import timedelta

from conftest import make_post
from instagram_os.analytics import AnalyticsCollector, engagement_rate
from instagram_os.client import PermissionDenied
from instagram_os.learning import LearningLoop, cta_type, hook_style
from instagram_os.store import utcnow


def publish_n(brand, publisher, specs):
    for i, (caption, meta) in enumerate(specs, 1):
        make_post(brand, f"{i:03d}", f"post{i}", caption=caption, meta=meta)
    from conftest import write_config
    write_config(brand, publishing={"max_posts_per_day": 100})
    publisher.cfg = brand.config
    publisher.ingest()
    publisher.validate_pending()
    publisher.publish_due()


def test_engagement_rate():
    assert engagement_rate({"likes": 10, "comments": 5, "shares": 3, "saved": 2, "reach": 200}) == 0.1
    assert engagement_rate({"likes": 10}) is None


def test_metrics_are_stored_historically(brand, store, fake, publisher, activity):
    publish_n(brand, publisher, [("One.", {})])
    mid = store.publications()[0]["media_id"]
    col = AnalyticsCollector(brand, store, fake, activity)
    fake.insights[mid] = {"reach": 100, "likes": 5, "comments": 1, "shares": 1, "saved": 3, "views": 150}
    col.collect()
    fake.insights[mid] = {"reach": 300, "likes": 20, "comments": 2, "shares": 2, "saved": 6, "views": 400}
    col.collect(now=utcnow() + timedelta(hours=7))
    hist = store.metric_history(mid, "reach")
    assert [v for _, v in hist] == [100, 300]
    assert store.latest_metrics(mid)["engagement_rate"] == 0.1
    assert store.metric_history("account", "followers_count")


def test_snapshot_spacing_respected(brand, store, fake, publisher, activity):
    publish_n(brand, publisher, [("One.", {})])
    col = AnalyticsCollector(brand, store, fake, activity)
    assert col.collect()["media"] == 1
    assert col.collect()["media"] == 0


def test_follower_change(brand, store, fake, activity):
    col = AnalyticsCollector(brand, store, fake, activity)
    store.add_metrics("account", {"followers_count": 100}, utcnow() - timedelta(days=3))
    store.add_metrics("account", {"followers_count": 130}, utcnow())
    assert col.follower_change(7) == 30


def test_insights_permission_error_is_logged_not_fatal(brand, store, fake, publisher, activity):
    publish_n(brand, publisher, [("One.", {})])
    fake.fail["get_media_insights"] = [PermissionDenied("missing instagram_business_manage_insights", code=10)]
    r = AnalyticsCollector(brand, store, fake, activity).collect()
    assert r["errors"] == 1


def test_feature_extraction():
    assert hook_style("5 ways to use Claude") == "number/list"
    assert hook_style("Are you still doing this by hand?") == "question"
    assert hook_style("Stop automating the wrong thing") == "contrarian/warning"
    assert hook_style("The one thing I learned") == "statement"
    assert cta_type("Save this for Monday.") == "save"
    assert cta_type("What would you add?") == "comment"


def test_weekly_review_recommends_without_touching_brand_files(brand, store, fake, publisher, activity):
    specs = [(f"{n} ways to automate invoicing.\n\nSave this.", {"pillar": "Automation habits"}) for n in (3, 4, 5)] + \
            [(f"Why Claude helps {x}.\n\nThoughts?", {"pillar": "Claude workflows"}) for x in ("ops", "sales", "hr")]
    publish_n(brand, publisher, specs)
    for p in store.publications():
        good = p["pillar"] == "Automation habits"
        store.add_metrics(p["media_id"], {"reach": 100, "likes": 20 if good else 4, "comments": 2, "shares": 2,
                                          "saved": 6 if good else 0,
                                          "engagement_rate": engagement_rate({"reach": 100, "likes": 20 if good else 4,
                                                                               "comments": 2, "shares": 2, "saved": 6 if good else 0})})
    before = (brand.path / "social-content-system.md").read_text()
    loop = LearningLoop(brand, store, activity)
    path = loop.write()
    text = path.read_text()
    assert "Automation habits" in text.split("do more of")[1].split("##")[0]
    assert "Claude workflows" in text.split("do less of")[1].split("##")[0]
    assert "number/list" in text and "Formats to test" in text
    assert (brand.instagram_dir / "recommendations" / "latest.md").read_text() == text
    assert (brand.path / "social-content-system.md").read_text() == before


def test_weekly_review_with_little_data_says_so(brand, store, activity):
    text = LearningLoop(brand, store, activity).report()
    assert "Not enough data yet" in text
