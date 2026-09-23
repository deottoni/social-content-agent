"""Shared fixtures. Every test runs against a throwaway brand folder and a fake API client —
never a real Instagram account, never the network."""
import json
import sys
from pathlib import Path

import pytest
import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from instagram_os.activity import ActivityLog  # noqa: E402
from instagram_os.config import load_brand  # noqa: E402
from instagram_os.fake import FakeInstagramClient  # noqa: E402
from instagram_os.store import Store  # noqa: E402

SOCIAL_SYSTEM = """# Social Content System — Test Brand

## Audience
Small-business owners curious about AI.

## Voice & tone
Calm, precise, a little dry. Never hypey. Never uses the word "revolutionary".

## Content pillars
1. AI for small business
2. Automation habits
3. Claude workflows

## Mode & cadence
- **mode:** hybrid

## Channels & post-type mix
- **Instagram:** carousel 60% / single image 30% / Reels 10%

## Format rules per channel
- **Instagram:** short captions, 5 hashtags

## Brand-safety no-go list
No financial or legal advice. No politics.
"""


class StaticHost:
    def url_for(self, path):
        return f"https://cdn.example.com/{path.parent.parent.name}/{path.name}"


@pytest.fixture
def brand(tmp_path, monkeypatch):
    brand_dir = tmp_path / "test-brand"
    (brand_dir / "instagram").mkdir(parents=True)
    (brand_dir / "social-content-system.md").write_text(SOCIAL_SYSTEM)
    (brand_dir / "visual-design-system.md").write_text("# Visual Design System — Test Brand\n")
    registry = tmp_path / "brands.local.json"
    registry.write_text(json.dumps({"test-brand": str(brand_dir)}))
    cfg = {
        "enabled": True,
        "api": {"mode": "live", "backoff_base_seconds": 0},
        "autonomy": {"publish_post": "auto"},
        "publishing": {"quiet_hours": {"start": None, "end": None}, "max_posts_per_day": 5,
                       "min_hours_between_posts": 0, "container_poll_seconds": 0},
        "reasoner": {"provider": "rules"},
    }
    (brand_dir / "instagram" / "config.yaml").write_text(yaml.safe_dump(cfg))
    for var in ("PUBLISH_MODE", "INSTAGRAM_API_MODE", "INSTAGRAM_LOGIN_TYPE"):
        monkeypatch.delenv(var, raising=False)
    env = {"INSTAGRAM_ACCESS_TOKEN": "test-token", "INSTAGRAM_ACCOUNT_ID": "17840000000000000"}
    ctx = load_brand("test-brand", registry_path=registry, env=env)
    return ctx


def write_config(ctx, **overrides):
    """Deep-merge overrides into the brand's config.yaml and reload."""
    from instagram_os.config import deep_merge, load_config
    path = ctx.instagram_dir / "config.yaml"
    cfg = deep_merge(yaml.safe_load(path.read_text()), overrides)
    path.write_text(yaml.safe_dump(cfg))
    ctx.config = load_config(ctx.path, env={})
    return ctx


def make_post(ctx, number="001", slug="five-ways", month=None, caption="Five ways to use Claude this week.\n\nSave this for Monday.\n\n#ai #smallbusiness",
              kind="image", size=(1080, 1350), status="ready", meta=None, variants=None):
    from datetime import date
    month = month or date.today().strftime("%Y-%m")
    folder = ctx.content_dir / month / f"{number}-{slug}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "caption.txt").write_text(caption)
    if kind == "image":
        for name in (variants or ["image"]):
            Image.new("RGB", size, "black").save(folder / f"{name}.png")
    elif kind == "carousel":
        for i in range(1, 4):
            Image.new("RGB", size, "black").save(folder / f"slide-{i}.png")
    elif kind == "reel":
        (folder / "reel.mp4").write_bytes(b"\x00" * 2048)
    if meta:
        (folder / "meta.md").write_text("\n".join(f"{k}: {v}" for k, v in meta.items()))
    index = ctx.content_dir / month / "_index.md"
    rows = index.read_text() if index.exists() else "| # | Name | Format | Status |\n|---|---|---|---|\n"
    index.write_text(rows + f"| {number} | {slug} | {kind} | {status} |\n")
    return folder


@pytest.fixture
def store(brand):
    s = Store(brand.db_path)
    yield s
    s.close()


@pytest.fixture
def activity(store, brand):
    return ActivityLog(store, brand.instagram_dir / "activity.log")


@pytest.fixture
def fake():
    return FakeInstagramClient()


@pytest.fixture
def publisher(brand, store, fake, activity):
    from instagram_os.publisher import Publisher
    return Publisher(brand, store, fake, activity, StaticHost(), sleep=lambda s: None)
