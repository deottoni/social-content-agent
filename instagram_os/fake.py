"""In-memory Instagram client for tests and credential-less dry runs. Never touches the network."""
import itertools

from .client import InstagramClient, MediaError, RateLimitError, UnsupportedOperation


class FakeInstagramClient(InstagramClient):
    def __init__(self, login_type="instagram", username="brand", account_id="17840000000000000"):
        self.login_type = login_type
        self.account_id = account_id
        self.username = username
        self._ids = itertools.count(1000)
        self.containers = {}      # id -> dict(status_code, kind, payload)
        self.media = {}           # id -> media dict
        self.comments = {}        # media_id -> [comment dicts]
        self.replies = []         # (comment_id, message, reply_id)
        self.hidden = set()
        self.insights = {}        # media_id -> dict
        self.account_insights = {}
        self.followers = 100
        self.discovery = {}       # username -> business_discovery payload
        self.hashtags = {}        # name -> (id, [media])
        self.publish_quota_used = 0
        self.publish_quota_total = 100
        self.calls = []
        # Failure injection: {"method_name": [exception, exception, ...]} consumed in order.
        self.fail = {}
        # Container status sequence returned by get_container_status for new containers.
        self.container_statuses = ["FINISHED"]

    def _hit(self, name, *args):
        self.calls.append((name, args))
        queue = self.fail.get(name)
        if queue:
            exc = queue.pop(0)
            if exc is not None:
                raise exc

    def _new_id(self):
        return str(next(self._ids))

    # account / media
    def get_account(self):
        self._hit("get_account")
        return {"id": self.account_id, "username": self.username, "followers_count": self.followers,
                "media_count": len(self.media)}

    def list_media(self, limit=25):
        self._hit("list_media", limit)
        return sorted(self.media.values(), key=lambda m: m["timestamp"], reverse=True)[:limit]

    def get_media(self, media_id):
        self._hit("get_media", media_id)
        return self.media.get(str(media_id).replace("dryrun-", ""), {"id": media_id})

    # publishing
    def _container(self, kind, payload):
        cid = self._new_id()
        self.containers[cid] = {"kind": kind, "payload": payload, "statuses": list(self.container_statuses)}
        return cid

    def create_image_container(self, image_url, caption=None, is_carousel_item=False):
        self._hit("create_image_container", image_url, caption)
        if not str(image_url).startswith("https://"):
            raise MediaError("image_url must be a public https URL", code=9004)
        return self._container("IMAGE", {"image_url": image_url, "caption": caption, "child": is_carousel_item})

    def create_reels_container(self, video_url, caption, cover_url=None, share_to_feed=True):
        self._hit("create_reels_container", video_url, caption)
        return self._container("REELS", {"video_url": video_url, "caption": caption})

    def create_carousel_container(self, children, caption):
        self._hit("create_carousel_container", children, caption)
        return self._container("CAROUSEL", {"children": children, "caption": caption})

    def get_container_status(self, container_id):
        self._hit("get_container_status", container_id)
        c = self.containers[container_id]
        status = c["statuses"].pop(0) if len(c["statuses"]) > 1 else c["statuses"][0]
        return status, status

    def publish_container(self, container_id):
        self._hit("publish_container", container_id)
        c = self.containers[container_id]
        if c.get("published"):
            raise MediaError("container already published", code=9007)
        if self.publish_quota_used >= self.publish_quota_total:
            raise RateLimitError("publishing limit reached", subcode=2207042)
        c["published"] = True
        self.publish_quota_used += 1
        mid = self._new_id()
        product = "REELS" if c["kind"] == "REELS" else "FEED"
        media_type = {"IMAGE": "IMAGE", "REELS": "VIDEO", "CAROUSEL": "CAROUSEL_ALBUM"}[c["kind"]]
        self.media[mid] = {"id": mid, "caption": c["payload"].get("caption"), "media_type": media_type,
                           "media_product_type": product, "permalink": f"https://www.instagram.com/p/{mid}/",
                           "timestamp": f"2026-09-23T{len(self.media):02d}:00:00+0000",
                           "like_count": 0, "comments_count": 0}
        return mid

    def get_publishing_limit(self):
        self._hit("get_publishing_limit")
        return {"used": self.publish_quota_used, "total": self.publish_quota_total}

    # comments
    def add_comment(self, media_id, text, username="someone", user_id="u1", comment_id=None, parent_id=None):
        cid = comment_id or self._new_id()
        c = {"id": cid, "text": text, "username": username, "from": {"id": user_id, "username": username},
             "timestamp": "2026-09-23T10:00:00+0000", "replies": {"data": []}}
        if parent_id:
            c["parent_id"] = parent_id
        self.comments.setdefault(media_id, []).append(c)
        return cid

    def list_comments(self, media_id, limit=50):
        self._hit("list_comments", media_id)
        return list(self.comments.get(media_id, []))[:limit]

    def reply_to_comment(self, comment_id, message):
        self._hit("reply_to_comment", comment_id, message)
        rid = self._new_id()
        self.replies.append((comment_id, message, rid))
        for comments in self.comments.values():
            for c in comments:
                if c["id"] == comment_id:
                    c["replies"]["data"].append({"id": rid, "text": message, "username": self.username,
                                                 "from": {"id": self.account_id, "username": self.username}})
        return rid

    def hide_comment(self, comment_id, hide=True):
        self._hit("hide_comment", comment_id)
        (self.hidden.add if hide else self.hidden.discard)(comment_id)
        return True

    # insights
    def get_media_insights(self, media_id, product_type="FEED"):
        self._hit("get_media_insights", media_id)
        return dict(self.insights.get(media_id, {}))

    def get_account_insights(self, since=None, until=None):
        self._hit("get_account_insights")
        return dict(self.account_insights)

    # discovery
    def _require_facebook(self, feature):
        if self.login_type != "facebook":
            raise UnsupportedOperation(f"{feature} requires Facebook Login")

    def business_discovery(self, username, media_limit=5):
        self._hit("business_discovery", username)
        self._require_facebook("Business Discovery")
        return self.discovery.get(username, {})

    def hashtag_search(self, hashtag):
        self._hit("hashtag_search", hashtag)
        self._require_facebook("Hashtag Search")
        return self.hashtags.get(hashtag, (None, []))[0]

    def hashtag_recent_media(self, hashtag_id, limit=10):
        self._hit("hashtag_recent_media", hashtag_id)
        for hid, media in self.hashtags.values():
            if hid == hashtag_id:
                return media[:limit]
        return []

    def refresh_token(self):
        self._hit("refresh_token")
        return {"access_token": "new-token", "expires_in": 5184000}

    def debug_token(self):
        return None
