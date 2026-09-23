"""Autonomy boundaries, publishing caps, and quiet hours."""
from datetime import time, timedelta, timezone

from .models import ContentType
from .store import parse_iso, utcnow


def _tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # missing tzdata: fall back to UTC rather than crash a scheduled run
        return timezone.utc


def in_quiet_hours(cfg, now=None):
    q = cfg["publishing"].get("quiet_hours") or {}
    if not q.get("start") or not q.get("end"):
        return False
    now = (now or utcnow()).astimezone(_tz(cfg["publishing"].get("timezone", "UTC")))
    start = time.fromisoformat(q["start"])
    end = time.fromisoformat(q["end"])
    t = now.time()
    return (start <= t or t < end) if start > end else (start <= t < end)


def publish_blockers(cfg, store, content_type, now=None):
    """Reasons a publish cannot happen right now (empty list = allowed)."""
    now = now or utcnow()
    p = cfg["publishing"]
    reasons = []
    if in_quiet_hours(cfg, now):
        reasons.append(f"quiet hours {p['quiet_hours']['start']}-{p['quiet_hours']['end']} {p.get('timezone', 'UTC')}")
    last_day = store.published_since(now - timedelta(days=1))
    if len(last_day) >= p["max_posts_per_day"]:
        reasons.append(f"max_posts_per_day={p['max_posts_per_day']} reached")
    if content_type == ContentType.REELS.value or content_type == ContentType.REELS:
        week = store.published_since(now - timedelta(days=7), ContentType.REELS.value)
        if len(week) >= p["max_reels_per_week"]:
            reasons.append(f"max_reels_per_week={p['max_reels_per_week']} reached")
    if last_day and p.get("min_hours_between_posts"):
        latest = max(parse_iso(r["published_at"]) for r in last_day)
        if now - latest < timedelta(hours=p["min_hours_between_posts"]):
            reasons.append(f"min_hours_between_posts={p['min_hours_between_posts']}")
    return reasons


def boundaries_table(ctx):
    """Human-readable AUTONOMOUS vs HUMAN split, straight from config."""
    from .config import LOCKED_HUMAN_ACTIONS
    rows = []
    for action in sorted(set(ctx.config["autonomy"]) | LOCKED_HUMAN_ACTIONS):
        value = ctx.autonomy(action)
        note = "locked: not supported by the official API" if action in LOCKED_HUMAN_ACTIONS else ""
        rows.append((action, value, note))
    return rows
