"""Publishing engine: content package -> validation -> queue -> Instagram -> recorded media.

Duplicate-publish protection, in layers:
  1. content_id (month/post folder) is UNIQUE; a PUBLISHED row is terminal and never re-queued.
  2. Posts marked `posted` in _index.md / meta.md are never ingested.
  3. An identical caption+media hash already PUBLISHED under another folder is held for review.
  4. QUEUED -> PUBLISHING is an atomic compare-and-set, so two runners cannot both claim a post.
  5. media_publish is never auto-retried; any failure after it is sent is reconciled against
     the account's recent media (caption match) before anything is retried.
  6. Dry-run never writes PUBLISHED, so a rehearsal can't block or fake a real publish.
"""
import json
import time
from datetime import timedelta

from .client import (AuthError, InstagramAPIError, MediaError, RateLimitError, TransientError)
from .content import discover_ready_posts, load_post, mark_posted_in_index
from .media_host import MediaHostError, MediaNotReachable
from .models import ContentType, PublicationStatus as S
from .policy import publish_blockers
from .store import iso, parse_iso, utcnow
from .validation import validate_artifact


class StopRun(Exception):
    """Abort the remaining queue for this run (rate limit, auth failure)."""


def _norm(text):
    return " ".join((text or "").split())


class Publisher:
    def __init__(self, ctx, store, client, activity, media_host, sleep=time.sleep):
        self.ctx, self.store, self.client, self.log = ctx, store, client, activity
        self.media_host = media_host
        self.cfg = ctx.config
        self.sleep = sleep

    # ------------------------------------------------------------------ ingest
    def ingest(self):
        """Pick up posts a human marked ready. Returns list of (content_id, action)."""
        results = []
        posts = discover_ready_posts(self.ctx.content_dir, self.cfg["publishing"]["ingest_months_back"])
        for art in posts:
            existing = self.store.get_publication(art.content_id)
            h = art.content_hash
            fields = dict(content_hash=h, content_type=art.content_type.value, caption=art.caption,
                          asset_paths=[str(p) for p in art.assets], topic=art.topic,
                          pillar=art.meta.get("pillar"), hook=art.hook, cta=art.cta,
                          format=art.meta.get("format") or art.content_type.value.lower(),
                          origin=art.meta.get("origin", "pipeline"))
            if existing is None:
                pub_id = self.store.insert_publication(dict(fields, content_id=art.content_id))
                self.log.record("CONTENT", f"Ingested {art.content_type.value.lower()} “{art.content_id}”",
                                reason="marked ready in content-packages", tool="content.ingest", ref=art.content_id)
                self._flag_duplicate_content(pub_id, h)
                results.append((art.content_id, "ingested"))
            elif existing["status"] in (S.PUBLISHED.value, S.PUBLISHING.value):
                continue
            elif existing["content_hash"] != h:
                fields["asset_paths"] = json.dumps(fields["asset_paths"])
                self.store.update_publication(existing["id"], **fields)
                if existing["status"] != S.DRAFT.value:
                    self.store.transition_publication(existing["id"], S.DRAFT,
                                                      "content changed on disk — needs re-validation/approval")
                self.log.record("CONTENT", f"Content changed for “{art.content_id}”, reset to DRAFT",
                                tool="content.ingest", ref=art.content_id)
                results.append((art.content_id, "updated"))
        return results

    def _flag_duplicate_content(self, pub_id, content_hash):
        dup = self.store.conn.execute(
            "SELECT content_id FROM publications WHERE content_hash=? AND status='PUBLISHED' AND id != ?",
            (content_hash, pub_id)).fetchone()
        if dup:
            self.store.transition_publication(pub_id, S.REQUIRES_REVIEW,
                                              f"identical content already published as {dup['content_id']}")

    # ---------------------------------------------------------------- validate
    def validate_pending(self):
        out = []
        for pub in self.store.publications([S.DRAFT]):
            out.append(self.validate(pub))
        return out

    def validate(self, pub):
        art = load_post(self._folder(pub))
        if art.content_hash != pub["content_hash"]:
            self.ingest()
            pub = self.store.get_publication(pub_id=pub["id"])
            art = load_post(self._folder(pub))
        result = validate_artifact(art, self.cfg)
        if not result.ok:
            reason = "; ".join(result.errors)
            self.store.transition_publication(pub["id"], S.REQUIRES_REVIEW, reason)
            self.log.record("VALIDATION", f"“{pub['content_id']}” needs review", reason=reason,
                            tool="validation", approval_required=True, ref=pub["content_id"])
            return pub["content_id"], S.REQUIRES_REVIEW
        self.store.transition_publication(pub["id"], S.VALIDATED, "; ".join(result.warnings) or None)
        self.log.record("VALIDATION", f"Passed validation “{pub['content_id']}”",
                        reason="; ".join(result.warnings) or "all checks passed", tool="validation",
                        ref=pub["content_id"])
        mode = self.ctx.autonomy("publish_post")
        if mode == "auto":
            self.store.transition_publication(pub["id"], S.QUEUED, "auto-queued (autonomy.publish_post=auto)")
            self.log.record("QUEUE", f"Auto-queued “{pub['content_id']}”", reason="publish_post=auto",
                            approval_required=False, ref=pub["content_id"])
            return pub["content_id"], S.QUEUED
        return pub["content_id"], S.VALIDATED

    # ----------------------------------------------------------------- approve
    def approve(self, content_id, override=False, note=None):
        pub = self.store.get_publication(content_id)
        if pub is None:
            raise KeyError(f"unknown content id {content_id}")
        if pub["status"] == S.REQUIRES_REVIEW.value and not override:
            raise ValueError(f"{content_id} requires review ({pub['status_reason']}); fix it and re-run "
                             "validate, or approve with --override if a human has checked it")
        if pub["status"] not in (S.VALIDATED.value, S.REQUIRES_REVIEW.value, S.FAILED.value):
            raise ValueError(f"{content_id} is {pub['status']}; only VALIDATED/REQUIRES_REVIEW/FAILED can be approved")
        reason = "approved by human" + (" (override)" if override else "") + (f": {note}" if note else "")
        self.store.transition_publication(pub["id"], S.QUEUED, reason, approved_at=iso(), attempts=0)
        self.log.record("APPROVAL", f"Queued “{content_id}” for publishing", reason=reason,
                        approval_required=True, ref=content_id)

    def reject(self, content_id, note=None):
        pub = self.store.get_publication(content_id)
        if pub is None or pub["status"] == S.PUBLISHED.value:
            raise ValueError(f"{content_id} cannot be rejected")
        self.store.transition_publication(pub["id"], S.REQUIRES_REVIEW, f"rejected by human: {note or ''}".strip())
        self.log.record("APPROVAL", f"Rejected “{content_id}”", reason=note, approval_required=True, ref=content_id)

    # ----------------------------------------------------------------- publish
    def publish_due(self, limit=None):
        """Publish QUEUED posts, oldest first, while caps and quiet hours allow."""
        if self.ctx.autonomy("publish_post") == "off":
            return []
        self.reconcile_stuck()
        done = []
        for pub in self.store.publications([S.QUEUED]):
            if limit is not None and len(done) >= limit:
                break
            blockers = publish_blockers(self.cfg, self.store, pub["content_type"])
            if blockers:
                self.log.record("PUBLISH", f"Holding “{pub['content_id']}”", reason="; ".join(blockers),
                                ref=pub["content_id"])
                break
            try:
                done.append((pub["content_id"], self.publish_one(pub)))
            except StopRun as e:
                self.log.record("PUBLISH", "Stopped publishing run", reason=str(e), error=str(e))
                break
        return done

    def _check_quota(self):
        try:
            q = self.client.get_publishing_limit()
        except InstagramAPIError:
            return  # quota endpoint unavailable: our own caps still apply
        remaining = q["total"] - q["used"]
        if remaining < self.cfg["publishing"]["min_api_quota_remaining"]:
            raise StopRun(f"Instagram publishing quota low ({remaining} of {q['total']} left in 24h)")

    def publish_one(self, pub):
        if not self.store.transition_publication(pub["id"], S.PUBLISHING, "publishing", expect=[S.QUEUED],
                                                 attempts=pub["attempts"] + 1):
            return "skipped (claimed elsewhere)"
        pub = self.store.get_publication(pub_id=pub["id"])
        cid = pub["content_id"]
        try:
            self._check_quota()
            art = load_post(self._folder(pub))
            if art.content_hash != pub["content_hash"]:
                self.store.transition_publication(pub["id"], S.DRAFT, "content changed after approval")
                return "content changed — back to DRAFT"
            result = validate_artifact(art, self.cfg)
            if not result.ok:
                self.store.transition_publication(pub["id"], S.REQUIRES_REVIEW, "; ".join(result.errors))
                return "failed re-validation"
            urls = self._public_urls(result.prepared_assets)
            container = self._container(pub, art, urls)
            self._wait_for_container(pub, container)
            try:
                media_id = self.client.publish_container(container)
            except InstagramAPIError as e:
                # The request may have gone through even though we got an error back.
                media = self._find_published(pub)
                if media is None:
                    raise
                media_id = media["id"]
                self.log.record("PUBLISH", f"Reconciled “{cid}” after an ambiguous publish error",
                                error=str(e), ref=cid)
            return self._mark_published(pub, media_id, container, urls)
        except StopRun:
            self.store.transition_publication(pub["id"], S.QUEUED, "run stopped before publish",
                                              attempts=pub["attempts"] - 1)
            raise
        except MediaNotReachable as e:
            # Waiting on the brand's sync to public storage: not the post's fault, don't burn an attempt.
            self.store.transition_publication(pub["id"], S.QUEUED, f"waiting for media: {e}",
                                              attempts=pub["attempts"] - 1)
            self.log.record("PUBLISH", f"Media for “{cid}” not reachable yet — will retry", error=str(e), ref=cid)
            return "waiting for media"
        except MediaHostError as e:
            self.store.transition_publication(pub["id"], S.REQUIRES_REVIEW, str(e))
            self.log.record("PUBLISH", f"Cannot host media for “{cid}”", error=str(e), approval_required=True, ref=cid)
            return "media host error"
        except (RateLimitError, AuthError) as e:
            self.store.transition_publication(pub["id"], S.QUEUED, f"{type(e).__name__}: {e}",
                                              attempts=pub["attempts"] - 1)
            self.log.record("PUBLISH", f"{type(e).__name__} while publishing “{cid}”", error=str(e),
                            api_action="publish", ref=cid)
            raise StopRun(f"{type(e).__name__}: {e}") from None
        except (TransientError, MediaError, InstagramAPIError, TimeoutError) as e:
            return self._handle_failure(pub, e)

    def _container(self, pub, art, urls):
        # Reuse a container from a previous attempt if it is still usable.
        if pub["container_id"]:
            try:
                status, _ = self.client.get_container_status(pub["container_id"])
                if status in ("FINISHED", "IN_PROGRESS"):
                    return pub["container_id"]
            except InstagramAPIError:
                pass
        caption = art.caption
        if art.content_type == ContentType.IMAGE:
            container = self.client.create_image_container(urls[0], caption)
        elif art.content_type == ContentType.CAROUSEL:
            children = [self.client.create_image_container(u, is_carousel_item=True) for u in urls]
            for child in children:
                self._wait_for_container(pub, child)
            container = self.client.create_carousel_container(children, caption)
        else:
            cover = next((u for u in urls[1:] if u.lower().endswith(".jpg")), None)
            container = self.client.create_reels_container(urls[0], caption, cover_url=cover)
        self.store.update_publication(pub["id"], container_id=container, asset_urls=urls)
        self.log.record("PUBLISH", f"Created media container for “{pub['content_id']}”",
                        api_action="POST /{ig-user-id}/media", result=f"container {container}", ref=pub["content_id"])
        return container

    def _wait_for_container(self, pub, container):
        p = self.cfg["publishing"]
        deadline = time.monotonic() + p["container_timeout_seconds"]
        while True:
            status, detail = self.client.get_container_status(container)
            if status in (None, "FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                self.store.update_publication(pub["id"], container_id=None)
                raise MediaError(f"container {container} status {status}: {detail}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"container {container} still {status} after {p['container_timeout_seconds']}s")
            self.sleep(p["container_poll_seconds"])

    def _public_urls(self, paths):
        urls = []
        for path in paths:
            try:
                urls.append(self.media_host.url_for(path))
            except MediaHostError:
                if not self.client.dry_run:
                    raise
                urls.append(f"https://dry-run.invalid/{path.name}")
        return urls

    def _find_published(self, pub, window_hours=24):
        """Look for our caption among the account's recent media (post-crash / ambiguous error)."""
        if self.client.dry_run:
            return None
        try:
            recent = self.client.list_media(limit=10)
        except InstagramAPIError:
            return None
        cutoff = utcnow() - timedelta(hours=window_hours)
        for m in recent:
            ts = parse_iso(m.get("timestamp"))
            if _norm(m.get("caption")) == _norm(pub["caption"]) and (ts is None or ts >= cutoff):
                return m
        return None

    def _mark_published(self, pub, media_id, container, urls):
        cid = pub["content_id"]
        if self.client.dry_run:
            self.store.transition_publication(pub["id"], S.QUEUED, "dry-run: publish simulated OK",
                                              attempts=pub["attempts"] - 1, container_id=None)
            self.log.record("PUBLISH", f"[dry-run] Would publish “{cid}”", api_action="POST /{ig-user-id}/media_publish",
                            result=f"simulated media {media_id}", ref=cid)
            return "dry-run ok"
        try:
            media = self.client.get_media(media_id)
        except InstagramAPIError:
            media = {"id": media_id}
        self.store.transition_publication(
            pub["id"], S.PUBLISHED, "published", media_id=media_id, permalink=media.get("permalink"),
            published_at=iso(), api_response={"container_id": container, "media": media, "asset_urls": urls})
        mark_posted_in_index(self._folder(pub).parent, self._folder(pub).name[:3])
        self.log.record("PUBLISH", f"Published “{cid}” — Instagram media ID: {media_id}",
                        api_action="POST /{ig-user-id}/media_publish", result=media.get("permalink") or media_id,
                        approval_required=self.ctx.autonomy("publish_post") != "auto", ref=cid)
        return "published"

    def _handle_failure(self, pub, error):
        max_attempts = self.cfg["publishing"]["max_publish_attempts"]
        cid = pub["content_id"]
        permanent = isinstance(error, MediaError) or (type(error) is InstagramAPIError)
        if permanent or pub["attempts"] >= max_attempts:
            self.store.transition_publication(pub["id"], S.FAILED, f"{type(error).__name__}: {error}",
                                              last_error=str(error))
            self.log.record("PUBLISH", f"FAILED “{cid}” after {pub['attempts']} attempt(s)",
                            error=str(error), approval_required=True, ref=cid)
            return "failed"
        self.store.transition_publication(pub["id"], S.QUEUED, f"will retry: {error}", last_error=str(error))
        self.log.record("PUBLISH", f"Transient failure on “{cid}”, will retry next run "
                                   f"({pub['attempts']}/{max_attempts})", error=str(error), ref=cid)
        return "retry later"

    def reconcile_stuck(self, stale_after=timedelta(minutes=30)):
        """PUBLISHING rows left by a crash: published after all, or safe to retry?"""
        for pub in self.store.publications([S.PUBLISHING]):
            if utcnow() - parse_iso(pub["updated_at"]) < stale_after:
                continue
            media = self._find_published(pub, window_hours=72)
            if media:
                self._mark_published(pub, media["id"], pub["container_id"], [])
            else:
                self._handle_failure(pub, TransientError("interrupted during publishing"))

    def _folder(self, pub):
        return self.ctx.content_dir / pub["content_id"]
