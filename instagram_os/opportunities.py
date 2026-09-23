"""Watchlist monitoring -> scored, prioritized HUMAN engagement opportunities.

The official API cannot like or comment on third-party posts, so nothing here acts on
them. It reads what the API legitimately exposes (Business Discovery for professional
accounts, Hashtag Search — both Facebook Login only) or what a human pastes in, asks
Claude whether a thoughtful comment would be worth making, and queues the result for a
person to act on by hand.
"""
import math
import re
from datetime import timedelta

from .client import InstagramAPIError, UnsupportedOperation
from .models import OpportunityStatus as O
from .reasoner import brand_context, reply_guardrail_issues
from .store import iso, parse_iso, utcnow

HASHTAG_WEEKLY_LIMIT = 30  # Meta: 30 unique hashtags per account per rolling 7 days


def priority_for(score, thresholds):
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    return "LOW"


def score_signals(signals, weights):
    total = sum(weights.values()) or 1.0
    return round(sum(weights.get(k, 0) * max(0.0, min(1.0, v)) for k, v in signals.items()) / total, 4)


def activity_signal(comments_count, likes=None):
    c = comments_count or 0
    return min(1.0, math.log1p(c) / math.log1p(100))


def recency_signal(ts, ttl_hours, now=None):
    if ts is None:
        return 0.5
    age = ((now or utcnow()) - ts).total_seconds() / 3600
    return max(0.0, 1 - age / max(1, ttl_hours))


class OpportunityMonitor:
    def __init__(self, ctx, store, client, activity, reasoner):
        self.ctx, self.store, self.client, self.log, self.reasoner = ctx, store, client, activity, reasoner
        self.cfg = ctx.config["opportunities"]

    # ----------------------------------------------------------------- run
    def run(self):
        expired = self.expire()
        if self.ctx.autonomy("monitor_opportunities") == "off" or not self.cfg.get("enabled", True):
            return {"expired": expired, "skipped": "monitoring disabled"}
        candidates = []
        try:
            candidates += self._from_accounts()
            candidates += self._from_hashtags()
        except UnsupportedOperation as e:
            self.log.record("OPPORTUNITY", "Automatic watchlist monitoring unavailable",
                            reason=f"{e} — add opportunities by hand with `instagram opportunities add`")
        created = self._score_and_store(candidates)
        self.log.record("OPPORTUNITY", f"Watchlist scan: {len(candidates)} post(s) checked, {len(created)} opportunity(ies) queued",
                        tool="opportunities.monitor", result=f"expired {expired}")
        return {"checked": len(candidates), "created": len(created), "expired": expired}

    def expire(self, now=None):
        now = now or utcnow()
        n = 0
        for opp in self.store.opportunities([O.NEW, O.REVIEWED, O.APPROVED]):
            if opp["expires_at"] and parse_iso(opp["expires_at"]) < now:
                self.store.transition_opportunity(opp["id"], O.EXPIRED, "relevance window passed")
                n += 1
        return n

    def _from_accounts(self):
        out = []
        for username in self.cfg["watchlist"].get("accounts") or []:
            username = username.lstrip("@")
            try:
                bd = self.client.business_discovery(username, self.cfg["max_media_per_account"])
            except UnsupportedOperation:
                raise
            except InstagramAPIError as e:
                self.log.record("OPPORTUNITY", f"Business Discovery failed for @{username}", error=str(e),
                                api_action="GET business_discovery")
                continue
            for m in (bd.get("media") or {}).get("data", []):
                out.append(dict(m, account=bd.get("username") or username, source="account"))
        return out

    def _from_hashtags(self):
        tags = [t.lstrip("#").lower() for t in (self.cfg["watchlist"].get("hashtags") or [])]
        if not tags:
            return []
        known = self.store.hashtags_searched_since(utcnow() - timedelta(days=7))
        out = []
        for tag in tags[: self.cfg["max_hashtags_per_run"]]:
            hid = known.get(tag)
            if hid is None:
                if len(known) >= HASHTAG_WEEKLY_LIMIT:
                    self.log.record("OPPORTUNITY", f"Skipped #{tag}", reason="30 unique hashtags / 7 days limit reached")
                    continue
                try:
                    hid = self.client.hashtag_search(tag)
                except UnsupportedOperation:
                    raise
                except InstagramAPIError as e:
                    self.log.record("OPPORTUNITY", f"Hashtag search failed for #{tag}", error=str(e))
                    continue
                self.store.record_hashtag_search(tag, hid)
                known[tag] = hid
            if not hid:
                continue
            try:
                media = self.client.hashtag_recent_media(hid, self.cfg["max_media_per_hashtag"])
            except InstagramAPIError as e:
                self.log.record("OPPORTUNITY", f"Recent media failed for #{tag}", error=str(e))
                continue
            out.extend(dict(m, account=None, source=f"#{tag}") for m in media)
        return out

    # ------------------------------------------------------------- scoring
    def _score_and_store(self, candidates):
        context = brand_context(self.ctx)
        topics = self.cfg["watchlist"].get("topics") or []
        scored = []
        for post in candidates:
            key = f"ig:{post['id']}"
            if self.store.has_opportunity(key):
                continue
            opp = self.assess(post, context, topics)
            if opp:
                scored.append(opp)
            else:
                self.store.mark_seen(key)
        scored.sort(key=lambda o: o["score"], reverse=True)
        created = []
        for opp in scored[self.cfg["max_new_per_run"]:]:
            self.store.mark_seen(opp["dedupe_key"])  # below the per-run cut: don't resurface it later
        for opp in scored[: self.cfg["max_new_per_run"]]:
            opp_id = self.store.insert_opportunity(opp)
            if opp_id:
                created.append(opp_id)
                self.log.record("OPPORTUNITY", f"{opp['priority']} — @{opp['source_account'] or opp['signals'].get('source')}: "
                                f"{opp['content_summary'][:80]}", reason=opp["relevance_reason"],
                                confidence=opp["score"], approval_required=True, ref=str(opp_id))
        return created

    def assess(self, post, context=None, topics=None, now=None):
        now = now or utcnow()
        context = context or brand_context(self.ctx)
        if topics is None:
            topics = self.cfg["watchlist"].get("topics") or []
        try:
            a = self.reasoner.assess_opportunity(post, context, topics)
        except Exception as e:
            self.log.record("OPPORTUNITY", f"Could not assess post {post.get('id')}", error=f"{type(e).__name__}: {e}")
            return None
        if a.suggested_action == "skip":
            return None
        ts = parse_iso(post.get("timestamp"))
        ttl = self.cfg["default_ttl_hours"]
        account = post.get("account")
        signals = {
            "topical_relevance": a.topical_relevance,
            "audience_overlap": a.audience_overlap,
            "discussion_activity": activity_signal(post.get("comments_count")),
            "recency": recency_signal(ts, ttl, now),
            "relationship": float((self.cfg.get("relationships") or {}).get(account or "", 0)),
            "brand_value": a.brand_value,
            "comment_confidence": a.comment_confidence,
        }
        score = score_signals(signals, self.cfg["weights"])
        if score < self.cfg["min_score"]:
            return None
        comment = a.suggested_comment.strip()
        notes = None
        issues = reply_guardrail_issues(comment, max_chars=500) if comment else []
        if issues:
            notes, comment = "suggested comment dropped: " + "; ".join(issues), ""
        expires = min((ts or now) + timedelta(hours=ttl), now + timedelta(hours=ttl))
        return dict(
            dedupe_key=f"ig:{post['id']}" if post.get("id") else f"url:{post.get('permalink')}",
            source_account=account, source_media_id=post.get("id"), source_url=post.get("permalink"),
            source_timestamp=post.get("timestamp"), content_summary=a.summary, relevance_reason=a.why,
            score=score, priority=priority_for(score, self.cfg["priority_thresholds"]),
            opportunity_type=a.opportunity_type, suggested_action=a.suggested_action,
            suggested_comment=comment or None,
            signals=dict({k: round(v, 3) for k, v in signals.items()}, source=post.get("source", "manual")),
            expires_at=iso(expires), notes=notes)

    # ------------------------------------------------------- human-entered
    def add_manual(self, url, text, account=None, comments_count=None, timestamp=None):
        """For posts a human saw (or any brand on Instagram Login, where discovery APIs don't exist)."""
        m = re.search(r"instagram\.com/(?:p|reel)/([\w-]+)", url or "")
        post = {"id": f"manual-{m.group(1)}" if m else None, "permalink": url, "caption": text,
                "account": (account or "").lstrip("@") or None, "comments_count": comments_count,
                "timestamp": timestamp or iso(), "source": "manual"}
        opp = self.assess(post)
        if opp is None:
            return None
        return self.store.insert_opportunity(opp)
