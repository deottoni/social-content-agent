from datetime import timedelta

import pytest

from conftest import make_post, write_config
from instagram_os.client import AuthError, DryRunClient, RateLimitError, TransientError
from instagram_os.models import InvalidTransition, PublicationStatus as S
from instagram_os.store import iso, utcnow


def run_pipeline(publisher):
    publisher.ingest()
    publisher.validate_pending()
    return publisher.publish_due()


def test_image_post_publishes_and_records_metadata(brand, store, fake, publisher):
    folder = make_post(brand, meta={"pillar": "Claude workflows", "hook": "Five ways"})
    assert run_pipeline(publisher) == [(f"{folder.parent.name}/{folder.name}", "published")]

    pub = store.publications()[0]
    assert pub["status"] == "PUBLISHED"
    assert pub["media_id"] in fake.media
    assert pub["content_type"] == "IMAGE"
    assert pub["pillar"] == "Claude workflows" and pub["hook"] == "Five ways"
    assert pub["cta"] == "Save this for Monday."
    assert pub["published_at"] and pub["permalink"] and pub["api_response"]
    # Instagram only accepts JPEG: the PNG was converted before upload.
    assert fake.calls[0][0] == "get_publishing_limit"
    create = next(c for c in fake.calls if c[0] == "create_image_container")
    assert create[1][0].endswith("image.jpg")
    # The human-maintained calendar is updated.
    assert "| posted |" in (folder.parent / "_index.md").read_text()
    events = [e["to_status"] for e in store.publication_events(pub["id"])]
    assert events == ["DRAFT", "VALIDATED", "QUEUED", "PUBLISHING", "PUBLISHED"]


def test_carousel_and_reel(brand, store, fake, publisher):
    write_config(brand, validation={"video_extensions": [".mp4"]})
    make_post(brand, "001", "carousel", kind="carousel")
    make_post(brand, "002", "reel", kind="reel", caption="A reel.")
    publisher.cfg = brand.config
    publisher.ingest()
    publisher.validate_pending()
    assert [r for _, r in publisher.publish_due()] == ["published", "published"]
    kinds = sorted(c["kind"] for c in fake.containers.values())
    assert kinds == ["CAROUSEL", "IMAGE", "IMAGE", "IMAGE", "REELS"]


def test_never_publishes_twice(brand, store, fake, publisher):
    make_post(brand)
    run_pipeline(publisher)
    # Second run, even after someone flips the calendar back to ready.
    idx = next(brand.content_dir.glob("*/_index.md"))
    idx.write_text(idx.read_text().replace("posted", "ready"))
    assert run_pipeline(publisher) == []
    assert len(fake.media) == 1
    with pytest.raises(InvalidTransition):
        store.transition_publication(store.publications()[0]["id"], S.QUEUED)


def test_posts_marked_posted_by_hand_are_never_ingested(brand, store, publisher):
    make_post(brand, status="posted")
    assert publisher.ingest() == []


def test_identical_content_in_another_folder_is_held(brand, store, fake, publisher):
    make_post(brand, "001", "a")
    run_pipeline(publisher)
    make_post(brand, "002", "a-copy")
    run_pipeline(publisher)
    copy = store.get_publication(next(p["content_id"] for p in store.publications() if p["content_id"].endswith("a-copy")))
    assert copy["status"] == "REQUIRES_REVIEW" and "identical content" in copy["status_reason"]
    assert len(fake.media) == 1


def test_concurrent_claim_only_one_wins(brand, store, publisher):
    make_post(brand)
    publisher.ingest()
    publisher.validate_pending()
    pub = store.publications([S.QUEUED])[0]
    assert store.transition_publication(pub["id"], S.PUBLISHING, expect=[S.QUEUED]) is True
    assert publisher.publish_one(pub) == "skipped (claimed elsewhere)"


def test_approval_mode_waits_for_human(brand, store, fake, publisher):
    write_config(brand, autonomy={"publish_post": "approval"})
    publisher.cfg = brand.config
    folder = make_post(brand)
    assert run_pipeline(publisher) == []
    assert store.publications()[0]["status"] == "VALIDATED"
    publisher.approve(f"{folder.parent.name}/{folder.name}")
    assert [r for _, r in publisher.publish_due()] == ["published"]


def test_publish_mode_env_override(brand, monkeypatch):
    from instagram_os.config import load_config
    assert load_config(brand.path, env={"PUBLISH_MODE": "approval"})["autonomy"]["publish_post"] == "approval"


def test_validation_blocks_bad_content(brand, store, fake, publisher):
    make_post(brand, "001", "square-ish", size=(1080, 500))               # 2.16:1 too wide
    make_post(brand, "002", "tbd", caption="Coming soon [TBD]")
    make_post(brand, "003", "flagged", meta={"review": "flagged"})
    make_post(brand, "004", "tags", caption=" ".join(f"#t{i}" for i in range(31)))
    make_post(brand, "005", "variants", variants=["image-black", "image-white"])
    run_pipeline(publisher)
    reasons = {p["content_id"].split("/")[1].split("-", 1)[1]: p["status_reason"] for p in store.publications()}
    assert all(p["status"] == "REQUIRES_REVIEW" for p in store.publications())
    assert "aspect ratio" in reasons["square-ish"]
    assert "placeholder" in reasons["tbd"]
    assert "flagged" in reasons["flagged"]
    assert "31 hashtags" in reasons["tags"]
    assert "variant" in reasons["variants"]
    assert fake.media == {}


def test_requires_review_needs_override(brand, store, publisher):
    folder = make_post(brand, caption="[TBD]")
    run_pipeline(publisher)
    cid = f"{folder.parent.name}/{folder.name}"
    with pytest.raises(ValueError):
        publisher.approve(cid)
    publisher.approve(cid, override=True)
    assert store.get_publication(cid)["status"] == "QUEUED"


def test_content_change_resets_to_draft(brand, store, publisher):
    write_config(brand, autonomy={"publish_post": "approval"})
    publisher.cfg = brand.config
    folder = make_post(brand)
    run_pipeline(publisher)
    (folder / "caption.txt").write_text("A different caption.")
    publisher.ingest()
    assert store.publications()[0]["status"] == "DRAFT"


def test_caps_and_quiet_hours(brand, store, fake, publisher):
    write_config(brand, publishing={"max_posts_per_day": 1})
    publisher.cfg = brand.config
    make_post(brand, "001", "one")
    make_post(brand, "002", "two", caption="Second post.")
    run_pipeline(publisher)
    assert len(fake.media) == 1
    assert store.get_publication(store.publications()[1]["content_id"])["status"] == "QUEUED"

    write_config(brand, publishing={"max_posts_per_day": 5, "quiet_hours": {"start": "00:00", "end": "23:59"}})
    publisher.cfg = brand.config
    assert publisher.publish_due() == []


def test_reels_weekly_cap(brand, store, fake, publisher):
    write_config(brand, publishing={"max_reels_per_week": 1}, validation={"video_extensions": [".mp4"]})
    publisher.cfg = brand.config
    make_post(brand, "001", "r1", kind="reel", caption="r1")
    make_post(brand, "002", "r2", kind="reel", caption="r2")
    run_pipeline(publisher)
    assert len(fake.media) == 1


def test_transient_errors_retry_then_fail(brand, store, fake, publisher):
    write_config(brand, publishing={"max_publish_attempts": 2})
    publisher.cfg = brand.config
    make_post(brand)
    fake.fail["create_image_container"] = [TransientError("503"), TransientError("503")]
    run_pipeline(publisher)
    pub = store.publications()[0]
    assert pub["status"] == "QUEUED" and pub["attempts"] == 1
    publisher.publish_due()
    assert store.publications()[0]["status"] == "FAILED"


def test_rate_limit_stops_run_and_keeps_queue(brand, store, fake, publisher):
    make_post(brand, "001", "a")
    make_post(brand, "002", "b", caption="b")
    fake.fail["create_image_container"] = [RateLimitError("limit", code=4)]
    run_pipeline(publisher)
    assert [p["status"] for p in store.publications()] == ["QUEUED", "QUEUED"]
    assert [p["attempts"] for p in store.publications()] == [0, 0]
    assert sum(1 for c in fake.calls if c[0] == "create_image_container") == 1


def test_expired_token_stops_run(brand, store, fake, publisher):
    make_post(brand)
    fake.fail["create_image_container"] = [AuthError("Error validating access token", code=190)]
    run_pipeline(publisher)
    assert store.publications()[0]["status"] == "QUEUED"
    assert "AuthError" in store.publications()[0]["status_reason"]


def test_low_api_quota_stops_before_publishing(brand, store, fake, publisher):
    fake.publish_quota_used = 98
    make_post(brand)
    run_pipeline(publisher)
    assert fake.media == {} and store.publications()[0]["status"] == "QUEUED"


def test_ambiguous_publish_error_is_reconciled_not_retried(brand, store, fake, publisher):
    make_post(brand)
    real_publish = fake.publish_container

    def publish_then_lose_response(cid):
        real_publish(cid)
        raise TransientError("connection reset")
    fake.publish_container = publish_then_lose_response
    run_pipeline(publisher)
    assert len(fake.media) == 1
    assert store.publications()[0]["status"] == "PUBLISHED"


def test_crash_recovery_of_stuck_publishing_row(brand, store, fake, publisher):
    make_post(brand)
    publisher.ingest()
    publisher.validate_pending()
    pub = store.publications()[0]
    store.transition_publication(pub["id"], S.PUBLISHING)
    store.conn.execute("UPDATE publications SET updated_at=? WHERE id=?", (iso(utcnow() - timedelta(hours=1)), pub["id"]))
    publisher.reconcile_stuck()
    assert store.publications()[0]["status"] == "QUEUED"


def test_container_error_marks_failed(brand, store, fake, publisher):
    make_post(brand)
    fake.container_statuses = ["IN_PROGRESS", "ERROR"]
    run_pipeline(publisher)
    assert store.publications()[0]["status"] == "FAILED"


def test_dry_run_never_marks_published(brand, store, fake, activity):
    from instagram_os.publisher import Publisher
    from conftest import StaticHost
    pub = Publisher(brand, store, DryRunClient(fake), activity, StaticHost(), sleep=lambda s: None)
    make_post(brand)
    assert [r for _, r in run_pipeline(pub)] == ["dry-run ok"]
    assert store.publications()[0]["status"] == "QUEUED"
    assert fake.media == {}  # the real client never saw a write
    assert "[dry-run] Would publish" in (brand.instagram_dir / "activity.log").read_text()


def test_missing_media_host_requires_review(brand, store, fake, activity):
    from instagram_os.media_host import NoMediaHost
    from instagram_os.publisher import Publisher
    pub = Publisher(brand, store, fake, activity, NoMediaHost(), sleep=lambda s: None)
    make_post(brand)
    run_pipeline(pub)
    p = store.publications()[0]
    assert p["status"] == "REQUIRES_REVIEW" and "media host" in p["status_reason"]


def test_unsynced_media_waits_in_queue_without_burning_attempts(brand, store, fake, activity):
    from instagram_os.media_host import MediaNotReachable
    from instagram_os.publisher import Publisher

    class Lagging:
        calls = 0

        def url_for(self, path):
            Lagging.calls += 1
            raise MediaNotReachable("404")
    pub = Publisher(brand, store, fake, activity, Lagging(), sleep=lambda s: None)
    make_post(brand)
    run_pipeline(pub)
    pub.publish_due()
    p = store.publications()[0]
    assert p["status"] == "QUEUED" and p["attempts"] == 0 and "waiting for media" in p["status_reason"]


def test_switching_to_auto_picks_up_already_validated_posts(brand, store, fake, publisher):
    write_config(brand, autonomy={"publish_post": "approval"})
    publisher.cfg = brand.config
    make_post(brand)
    run_pipeline(publisher)
    assert store.publications()[0]["status"] == "VALIDATED"
    write_config(brand, autonomy={"publish_post": "auto"})
    publisher.cfg = brand.config
    assert [r for _, r in publisher.publish_due()] == ["published"]
