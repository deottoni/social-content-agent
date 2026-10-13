"""Reference harvesting: pull real posts from chosen accounts and hashtags into the brand folder.

Read-only. Nothing here posts, likes, follows, or comments. Uses Business Discovery (other
Creator/Business accounts) and Hashtag Search (`top_media`), both of which need
`api.login_type: facebook`. Works while publishing is still `enabled: false`.

Output, per post (brand data, so it lives in the brand folder, never in this repo):

    <brand-path>/references/<account>/<media-id>/   slide-N.jpg, caption.txt, post.json
    <brand-path>/references/hashtag-<tag>/<media-id>/  (hashtag results carry no username)
    <brand-path>/references/index.json               one entry per harvested media id

`index.json` is the dedup record (a media id is never downloaded twice) and tracks which
references the `reference-analyst` agent has already read (`analyzed_at`).
See docs/reference-scan-plan.md.
"""
import json
import statistics
import urllib.request
from datetime import timedelta
from pathlib import Path

from .client import (REFERENCE_MEDIA_FIELDS, AuthError, InstagramAPIError, PermissionDenied, RateLimitError,
                     UnsupportedOperation)
from .opportunities import HASHTAG_WEEKLY_LIMIT
from .store import iso, utcnow

# A missing permission fails every account the same way, so it stops the run instead of being
# reported per account as "unreadable".
FATAL = (AuthError, RateLimitError, UnsupportedOperation, PermissionDenied)
PERMISSION_HINT = ("token lacks a permission: Business Discovery needs instagram_basic, instagram_manage_insights "
                   "and pages_read_engagement (+ ads_read when the Page is in a business portfolio); "
                   "Hashtag Search also needs Meta's Instagram Public Content Access feature")

# Business Discovery can reject the nested children edge; fall back to cover images only.
FIELDS_WITHOUT_CHILDREN = REFERENCE_MEDIA_FIELDS.replace(",children{media_url,media_type}", "")
VIDEO_TYPES = {"VIDEO", "REELS"}


def urllib_fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
        return resp.read()


def _ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def engagement(post):
    likes, comments = post.get("like_count"), post.get("comments_count")
    if likes is None and comments is None:
        return None
    return (likes or 0) + (comments or 0)


def outperformance(posts):
    """Each post's engagement divided by its batch's median (one batch = one account or hashtag),
    so a post that beats its own account's usual numbers ranks high whatever the account's size."""
    values = [e for e in (engagement(p) for p in posts) if e is not None]
    median = statistics.median(values) if values else 0
    return {p["id"]: (round(engagement(p) / median, 2) if median and engagement(p) is not None else None)
            for p in posts}, median


class ReferenceLibrary:
    """The brand's `references/` folder and its index.json."""

    def __init__(self, brand_path):
        self.root = Path(brand_path) / "references"
        self.index_path = self.root / "index.json"
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    def __contains__(self, media_id):
        return str(media_id) in self.index

    def save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.index, indent=2, sort_keys=True))
        tmp.replace(self.index_path)

    def add(self, media_id, entry):
        self.index[str(media_id)] = entry

    def entries(self, unanalyzed=False, ids=None):
        rows = [dict(e, media_id=mid, folder=str(self.root.parent / e["path"])) for mid, e in self.index.items()
                if (not unanalyzed or not e.get("analyzed_at")) and (ids is None or mid in ids)]
        return sorted(rows, key=lambda e: (-(e.get("outperformance") or 0), e["media_id"]))

    def mark_analyzed(self, media_ids):
        now, done = iso(), []
        for mid in media_ids:
            if str(mid) in self.index:
                self.index[str(mid)]["analyzed_at"] = now
                done.append(str(mid))
        self.save()
        return done


class Harvester:
    def __init__(self, ctx, store, client, activity, fetch=None):
        self.ctx, self.store, self.client, self.log = ctx, store, client, activity
        self.cfg = ctx.config["research"]
        self.fetch = fetch or urllib_fetch
        self.library = ReferenceLibrary(ctx.path)

    # ------------------------------------------------------------- selection
    def accounts_for(self, shortlist=None):
        if not shortlist or shortlist == "all":
            names = []
            for n in [*(self.cfg.get("accounts") or []),
                      *(m for members in (self.cfg.get("shortlists") or {}).values() for m in members or [])]:
                if n.lstrip("@") not in names:
                    names.append(n.lstrip("@"))
            return names
        lists = self.cfg.get("shortlists") or {}
        if shortlist not in lists:
            raise KeyError(f"no research.shortlists.{shortlist} in config.yaml "
                           f"(have: {', '.join(sorted(lists)) or 'none'})")
        return [n.lstrip("@") for n in lists[shortlist] or []]

    # ------------------------------------------------------------- harvest
    def harvest(self, accounts=(), hashtags=(), per_account=None, per_hashtag=None):
        """Returns {"sources": [row, ...], "new": [entry, ...], "stopped": reason|None}."""
        per_account = per_account or self.cfg["per_account"]
        per_hashtag = per_hashtag or self.cfg["per_hashtag"]
        report = {"sources": [], "new": [], "stopped": None}
        try:
            for username in accounts:
                report["sources"].append(self._harvest_account(username, per_account, report["new"]))
            for tag in hashtags:
                report["sources"].append(self._harvest_hashtag(tag.lstrip("#").lower(), per_hashtag, report["new"]))
        except RateLimitError as e:
            report["stopped"] = f"rate limit hit, run again in an hour: {e}"
        except PermissionDenied as e:
            report["stopped"] = f"{PERMISSION_HINT} ({e})"
        finally:
            self.library.save()
        report["new"].sort(key=lambda e: -(e.get("outperformance") or 0))
        self.log.record("RESEARCH", f"Harvested {len(report['new'])} new reference post(s)",
                        tool="research.harvest", api_action="GET business_discovery / top_media",
                        result=", ".join(f"{r['source']}:{r['status']}" for r in report["sources"]),
                        error=report["stopped"])
        return report

    def _harvest_account(self, username, limit, new):
        row = _row(f"@{username}")
        try:
            try:
                bd = self.client.business_discovery(username, limit, REFERENCE_MEDIA_FIELDS)
            except InstagramAPIError as e:
                if isinstance(e, FATAL) or e.code != 100:
                    raise
                bd = self.client.business_discovery(username, limit, FIELDS_WITHOUT_CHILDREN)
                row["note"] = "carousel children unavailable; cover images only"
        except FATAL:
            raise
        except InstagramAPIError as e:
            row.update(status="unreadable", note=f"not a Creator/Business account, or wrong username ({e})")
            return row
        posts = (bd.get("media") or {}).get("data", [])
        if not bd or not posts:
            row.update(status="empty", note="no media returned (private, personal, or no posts)")
            return row
        account = bd.get("username") or username
        self._store_batch(posts, account, account, f"@{account}", bd.get("followers_count"), row, new)
        return row

    def _harvest_hashtag(self, tag, limit, new):
        row = _row(f"#{tag}")
        known = self.store.hashtags_searched_since(utcnow() - timedelta(days=7))
        hid = known.get(tag)
        if tag not in known:
            if len(known) >= HASHTAG_WEEKLY_LIMIT:
                row.update(status="skipped", note="30 unique hashtags / 7 days limit reached")
                return row
            try:
                hid = self.client.hashtag_search(tag)
            except FATAL:
                raise
            except InstagramAPIError as e:
                row.update(status="unreadable", note=str(e))
                return row
            self.store.record_hashtag_search(tag, hid)
        if not hid:
            row.update(status="empty", note="hashtag not found")
            return row
        try:
            posts = self.client.hashtag_top_media(hid, limit, REFERENCE_MEDIA_FIELDS)
        except FATAL:
            raise
        except InstagramAPIError as e:
            row.update(status="unreadable", note=str(e))
            return row
        self._store_batch(posts, None, f"hashtag-{tag}", f"#{tag}", None, row, new)
        return row

    def _store_batch(self, posts, account, folder_name, source, followers, row, new):
        ratios, median = outperformance(posts)
        row["fetched"] = len(posts)
        row["median_engagement"] = median
        for post in posts:
            mid = str(post["id"])
            if mid in self.library:
                row["already"] += 1
                continue
            if post.get("media_type") in VIDEO_TYPES and not self.cfg["include_reels"]:
                row["reels_skipped"] += 1
                continue
            ratio = ratios.get(post["id"])
            if self.cfg["min_outperformance"] and (ratio or 0) < self.cfg["min_outperformance"]:
                row["below_threshold"] += 1
                continue
            folder = self.library.root / folder_name / mid
            try:
                images = self._download(post, folder)
            except Exception as e:  # noqa: BLE001 — one bad image must not stop the harvest
                for f in folder.glob("*"):
                    f.unlink()
                row["errors"] += 1
                row["note"] = f"download failed for {mid}: {e}"
                continue
            (folder / "caption.txt").write_text(post.get("caption") or "")
            meta = {k: post.get(k) for k in ("id", "media_type", "permalink", "timestamp", "like_count",
                                             "comments_count")}
            meta.update(account=account, source=source, followers_count=followers, outperformance=ratio,
                        account_median_engagement=median, images=images, harvested_at=iso())
            (folder / "post.json").write_text(json.dumps(meta, indent=2))
            entry = {"path": str(folder.relative_to(self.ctx.path)), "account": account, "source": source,
                     "permalink": post.get("permalink"), "timestamp": post.get("timestamp"),
                     "media_type": post.get("media_type"), "slides": len(images),
                     "engagement": engagement(post), "outperformance": ratio,
                     "harvested_at": meta["harvested_at"], "analyzed_at": None}
            self.library.add(mid, entry)
            new.append(dict(entry, media_id=mid, folder=str(folder)))
            row["new"] += 1

    def _download(self, post, folder):
        """Download every still image of a post. Video media is never downloaded (v1)."""
        if post.get("media_type") == "CAROUSEL_ALBUM" and (post.get("children") or {}).get("data"):
            urls = [c.get("media_url") for c in post["children"]["data"] if c.get("media_type") == "IMAGE"]
        elif post.get("media_type") in VIDEO_TYPES:
            urls = []
        else:
            urls = [post.get("media_url")]
        folder.mkdir(parents=True, exist_ok=True)
        names = []
        for n, url in enumerate([u for u in urls if u], start=1):
            data = self.fetch(url)
            name = f"slide-{n}{_ext(data)}"
            (folder / name).write_bytes(data)
            names.append(name)
        return names


def _row(source):
    return {"source": source, "status": "ok", "fetched": 0, "new": 0, "already": 0, "reels_skipped": 0,
            "below_threshold": 0, "errors": 0, "median_engagement": None, "note": ""}
