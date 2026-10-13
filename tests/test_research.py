import io
import json

import pytest
import yaml
from PIL import Image

from conftest import write_config
from instagram_os import cli
from instagram_os.client import GraphInstagramClient, InstagramAPIError, PermissionDenied, RateLimitError
from instagram_os.research import Harvester, ReferenceLibrary, outperformance
from instagram_os.scheduler import Runtime


def png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 10), "white").save(buf, "PNG")
    return buf.getvalue()


class Fetcher:
    def __init__(self, fail=()):
        self.urls, self.fail = [], set(fail)

    def __call__(self, url):
        self.urls.append(url)
        if url in self.fail:
            raise OSError("expired")
        return png()


def media(pid, likes, kind="IMAGE", comments=0, children=0):
    m = {"id": pid, "caption": f"caption {pid}", "media_type": kind, "like_count": likes,
         "comments_count": comments, "permalink": f"https://www.instagram.com/p/{pid}/",
         "timestamp": "2026-09-20T10:00:00+0000", "media_url": f"https://cdn.test/{pid}.jpg"}
    if children:
        m["children"] = {"data": [{"media_type": "IMAGE", "media_url": f"https://cdn.test/{pid}-{i}.jpg"}
                                  for i in range(1, children + 1)]}
    return m


@pytest.fixture
def fb(fake):
    fake.login_type = "facebook"
    fake.discovery["quotes"] = {"username": "quotes", "followers_count": 5000, "media": {"data": [
        media("1", 100), media("2", 300, kind="CAROUSEL_ALBUM", children=3), media("3", 50),
        media("4", 900, kind="VIDEO")]}}
    fake.discovery["fitness"] = {"username": "fitness", "followers_count": 90, "media": {"data": [
        media("10", 10), media("11", 20)]}}
    return fake


@pytest.fixture
def research_brand(brand):
    return write_config(brand, enabled=False, api={"login_type": "facebook"},
                        research={"accounts": ["quotes"], "shortlists": {"core": ["fitness", "@quotes"]}})


def harvester(ctx, store, client, activity, fetch=None):
    return Harvester(ctx, store, client, activity, fetch=fetch or Fetcher())


def test_harvest_downloads_images_and_carousels_skips_reels(research_brand, store, fb, activity):
    fetch = Fetcher()
    report = harvester(research_brand, store, fb, activity, fetch).harvest(["quotes"])
    row = report["sources"][0]
    assert (row["fetched"], row["new"], row["reels_skipped"]) == (4, 3, 1)
    root = research_brand.path / "references" / "quotes"
    assert sorted(p.name for p in (root / "2").iterdir()) == [
        "caption.txt", "post.json", "slide-1.png", "slide-2.png", "slide-3.png"]
    assert (root / "1" / "caption.txt").read_text() == "caption 1"
    meta = json.loads((root / "2" / "post.json").read_text())
    assert meta["followers_count"] == 5000 and meta["account"] == "quotes" and meta["images"][0] == "slide-1.png"
    assert not (root / "4").exists()                      # video never downloaded
    assert "https://cdn.test/2.jpg" not in fetch.urls      # carousel: children, not the cover
    # Ranked against the account's own median (100, 300, 50, 900 -> median 200).
    assert [e["media_id"] for e in report["new"]] == ["2", "1", "3"]
    assert report["new"][0]["outperformance"] == 1.5


def test_media_id_is_never_downloaded_twice(research_brand, store, fb, activity):
    harvester(research_brand, store, fb, activity).harvest(["quotes"])
    fetch = Fetcher()
    report = harvester(research_brand, store, fb, activity, fetch).harvest(["quotes"])
    assert report["new"] == [] and report["sources"][0]["already"] == 3 and fetch.urls == []


def test_include_reels_keeps_metadata_only(research_brand, store, fb, activity):
    write_config(research_brand, research={"include_reels": True})
    report = harvester(research_brand, store, fb, activity).harvest(["quotes"])
    reel = next(e for e in report["new"] if e["media_id"] == "4")
    assert reel["slides"] == 0 and (research_brand.path / reel["path"] / "caption.txt").exists()


def test_min_outperformance_filters(research_brand, store, fb, activity):
    write_config(research_brand, research={"min_outperformance": 1.0})
    report = harvester(research_brand, store, fb, activity).harvest(["quotes"])
    assert [e["media_id"] for e in report["new"]] == ["2"]
    assert report["sources"][0]["below_threshold"] == 2


def test_unreadable_account_does_not_stop_the_run(research_brand, store, fb, activity):
    fb.fail["business_discovery"] = [InstagramAPIError("Cannot find User", code=110)]
    report = harvester(research_brand, store, fb, activity).harvest(["personal_acct", "fitness"])
    assert report["sources"][0]["status"] == "unreadable"
    assert report["sources"][1]["new"] == 2


def test_empty_discovery_reported(research_brand, store, fb, activity):
    report = harvester(research_brand, store, fb, activity).harvest(["nobody"])
    assert report["sources"][0]["status"] == "empty"


def test_rate_limit_stops_and_keeps_what_was_saved(research_brand, store, fb, activity):
    fb.fail["business_discovery"] = [None, RateLimitError("too many calls", code=4)]
    report = harvester(research_brand, store, fb, activity).harvest(["quotes", "fitness"])
    assert "rate limit" in report["stopped"] and len(report["new"]) == 3
    assert len(ReferenceLibrary(research_brand.path).index) == 3


def test_missing_permission_stops_the_run(research_brand, store, fb, activity):
    fb.fail["business_discovery"] = [PermissionDenied("Application does not have permission", code=10)]
    report = harvester(research_brand, store, fb, activity).harvest(["quotes", "fitness"])
    assert "instagram_manage_insights" in report["stopped"] and report["sources"] == []
    assert sum(1 for c in fb.calls if c[0] == "business_discovery") == 1


def test_children_fallback_when_field_rejected(research_brand, store, fb, activity):
    fb.fail["business_discovery"] = [InstagramAPIError("(#100) Tried accessing nonexisting field", code=100)]
    report = harvester(research_brand, store, fb, activity).harvest(["quotes"])
    assert "cover images only" in report["sources"][0]["note"]
    fields = [c[1][1] for c in fb.calls if c[0] == "business_discovery"]
    assert "children" in fields[0] and "children" not in fields[1]


def test_failed_download_is_retried_next_run(research_brand, store, fb, activity):
    report = harvester(research_brand, store, fb, activity, Fetcher(fail={"https://cdn.test/1.jpg"})).harvest(["quotes"])
    assert report["sources"][0]["errors"] == 1 and "1" not in ReferenceLibrary(research_brand.path)
    report = harvester(research_brand, store, fb, activity).harvest(["quotes"])
    assert [e["media_id"] for e in report["new"]] == ["1"]


def test_hashtag_top_media_and_shared_weekly_budget(research_brand, store, fb, activity):
    fb.hashtags["smallbiz"] = ("h1", [media("20", 40), media("21", 10)])
    report = harvester(research_brand, store, fb, activity).harvest(hashtags=["#SmallBiz"])
    assert report["sources"][0]["new"] == 2
    assert (research_brand.path / "references" / "hashtag-smallbiz" / "20" / "slide-1.png").exists()
    assert report["new"][0]["source"] == "#smallbiz" and report["new"][0]["account"] is None
    assert any(c[0] == "hashtag_top_media" for c in fb.calls)
    for i in range(29):
        store.record_hashtag_search(f"tag{i}", f"x{i}")
    report = harvester(research_brand, store, fb, activity).harvest(hashtags=["another"])
    assert report["sources"][0]["status"] == "skipped"
    assert sum(1 for c in fb.calls if c[0] == "hashtag_search") == 1


def test_shortlists(research_brand, store, fb, activity):
    h = harvester(research_brand, store, fb, activity)
    assert h.accounts_for("core") == ["fitness", "quotes"]
    assert h.accounts_for() == ["quotes", "fitness"]
    with pytest.raises(KeyError):
        h.accounts_for("missing")


def test_library_tracks_analysis(research_brand, store, fb, activity):
    harvester(research_brand, store, fb, activity).harvest(["quotes"])
    lib = ReferenceLibrary(research_brand.path)
    assert [e["media_id"] for e in lib.entries(unanalyzed=True)] == ["2", "1", "3"]
    lib.mark_analyzed(["2", "nope"])
    assert [e["media_id"] for e in ReferenceLibrary(research_brand.path).entries(unanalyzed=True)] == ["1", "3"]


def test_outperformance_handles_hidden_likes():
    ratios, median = outperformance([{"id": "a"}, {"id": "b", "like_count": 10}, {"id": "c", "comments_count": 30}])
    assert median == 20 and ratios == {"a": None, "b": 0.5, "c": 1.5}


def test_graph_client_requests_reference_fields():
    seen = []

    def transport(method, url, params, timeout):
        seen.append((url, params))
        return 200, {"business_discovery": {}, "data": []}

    c = GraphInstagramClient("tok", "123", login_type="facebook", transport=transport)
    c.business_discovery("quotes", 20, "id,media_url,children{media_url,media_type}")
    c.hashtag_top_media("h1", 20)
    assert "media.limit(20){id,media_url,children{media_url,media_type}}" in seen[0][1]["fields"]
    assert seen[1][0].endswith("/h1/top_media") and "children{" in seen[1][1]["fields"]


def _cli_setup(research_brand, fb, monkeypatch):
    monkeypatch.setattr(cli, "Runtime", lambda ctx: Runtime(ctx, client=fb))
    monkeypatch.setattr("instagram_os.research.urllib_fetch", Fetcher())
    return ["--brand", "test-brand", "--registry", str(research_brand.path.parent / "brands.local.json")]


def test_cli_harvest_works_while_publishing_disabled(research_brand, fb, monkeypatch, capsys):
    args = _cli_setup(research_brand, fb, monkeypatch)
    assert cli.main(args + ["harvest", "--shortlist", "core"]) == 0
    out = capsys.readouterr().out
    assert "@fitness" in out and "5 new reference post(s)" in out
    assert cli.main(args + ["references", "--unanalyzed", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 5
    assert cli.main(args + ["references", "--mark-analyzed", rows[0]["media_id"]]) == 0
    assert cli.main(args + ["publish"]) == 2  # publishing still refused for a disabled brand


def test_cli_harvest_needs_facebook_login(research_brand, fb, monkeypatch, capsys):
    cfg = yaml.safe_load((research_brand.instagram_dir / "config.yaml").read_text())
    cfg["api"]["login_type"] = "instagram"
    (research_brand.instagram_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    args = _cli_setup(research_brand, fb, monkeypatch)
    assert cli.main(args + ["harvest"]) == 1
    assert "login_type: facebook" in capsys.readouterr().err
