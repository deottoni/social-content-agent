"""Metrics collection: historical snapshots for published media and the account.

Every run appends a snapshot (never overwrites), so growth curves and follower changes
can be computed later. Metrics join back to content metadata through the publications
table (topic, pillar, format, hook, CTA, caption, publish time).
"""
from datetime import timedelta

from .client import InstagramAPIError
from .store import parse_iso, utcnow

ENGAGEMENT_KEYS = ("likes", "comments", "shares", "saved")


def engagement_rate(m):
    reach = m.get("reach")
    if not reach:
        return None
    return round(sum(m.get(k) or 0 for k in ENGAGEMENT_KEYS) / reach, 5)


class AnalyticsCollector:
    def __init__(self, ctx, store, client, activity):
        self.ctx, self.store, self.client, self.log = ctx, store, client, activity
        self.cfg = ctx.config["analytics"]

    def collect(self, now=None):
        now = now or utcnow()
        if self.ctx.autonomy("collect_analytics") == "off":
            return {"skipped": "analytics disabled"}
        since = now - timedelta(days=self.cfg["media_lookback_days"])
        min_gap = timedelta(hours=self.cfg["snapshot_min_hours"])
        done, errors = 0, 0
        for pub in self.store.publications(["PUBLISHED"]):
            if not pub["media_id"] or parse_iso(pub["published_at"]) < since:
                continue
            last = self.store.last_snapshot_at(pub["media_id"])
            if last and now - last < min_gap:
                continue
            try:
                snap = self.snapshot_media(pub)
            except InstagramAPIError as e:
                errors += 1
                self.log.record("ANALYTICS", f"Insights failed for {pub['content_id']}", error=str(e),
                                api_action="GET /{media-id}/insights", ref=pub["content_id"])
                continue
            self.store.add_metrics(pub["media_id"], snap, now)
            done += 1
        account = self.snapshot_account(now)
        self.log.record("ANALYTICS", f"Collected metrics for {done} post(s)",
                        result=f"followers={account.get('followers_count')}", error=f"{errors} failed" if errors else None)
        return {"media": done, "errors": errors, "account": account}

    def snapshot_media(self, pub):
        product = "REELS" if pub["content_type"] == "REELS" else "FEED"
        media = self.client.get_media(pub["media_id"])
        insights = self.client.get_media_insights(pub["media_id"], product)
        snap = {
            "likes": insights.get("likes", media.get("like_count")),
            "comments": insights.get("comments", media.get("comments_count")),
            "shares": insights.get("shares"),
            "saved": insights.get("saved"),
            "reach": insights.get("reach"),
            "views": insights.get("views"),
            "total_interactions": insights.get("total_interactions"),
            "ig_reels_avg_watch_time": insights.get("ig_reels_avg_watch_time"),
        }
        snap["engagement_rate"] = engagement_rate(snap)
        age = utcnow() - parse_iso(pub["published_at"])
        snap["hours_since_publish"] = round(age.total_seconds() / 3600, 1)
        return snap

    def snapshot_account(self, now):
        out = {}
        try:
            acct = self.client.get_account()
            out.update(followers_count=acct.get("followers_count"), media_count=acct.get("media_count"))
        except InstagramAPIError as e:
            self.log.record("ANALYTICS", "Account fields unavailable", error=str(e))
        try:
            out.update(self.client.get_account_insights())
        except InstagramAPIError as e:
            self.log.record("ANALYTICS", "Account insights unavailable", error=str(e),
                            reason="needs instagram_business_manage_insights / instagram_manage_insights")
        numeric = {k: v for k, v in out.items() if isinstance(v, (int, float))}
        if numeric:
            self.store.add_metrics("account", numeric, now)
        return out

    def follower_change(self, days=7):
        hist = self.store.metric_history("account", "followers_count")
        if len(hist) < 2:
            return None
        cutoff = utcnow() - timedelta(days=days)
        base = next((v for t, v in hist if t >= cutoff), hist[0][1])
        return hist[-1][1] - base
