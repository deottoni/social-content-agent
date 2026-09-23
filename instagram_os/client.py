"""Instagram Platform API client.

Everything above this module talks to `InstagramClient`, never to HTTP. The only
methods here are ones the official Instagram Platform API supports (see
docs/instagram-api-limitations.md for what it does not). Liking posts, commenting on
third-party posts, following accounts, and starting DMs are deliberately absent.
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .activity import redact

HOSTS = {"instagram": "https://graph.instagram.com", "facebook": "https://graph.facebook.com"}

MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
COMMENT_FIELDS = "id,text,timestamp,username,from,parent_id,like_count,replies{id,text,timestamp,username,from}"

# Current media insights metrics (impressions/plays were retired in favour of `views`).
MEDIA_METRICS = {
    "FEED": ["reach", "likes", "comments", "shares", "saved", "views", "total_interactions"],
    "REELS": ["reach", "likes", "comments", "shares", "saved", "views", "total_interactions",
              "ig_reels_avg_watch_time"],
}
ACCOUNT_METRICS = ["reach", "views", "accounts_engaged", "total_interactions", "follows_and_unfollows"]

RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002, 80006}
AUTH_CODES = {102, 190}
PERMISSION_CODES = {10} | set(range(200, 300))
TRANSIENT_CODES = {1, 2}
PUBLISH_LIMIT_SUBCODE = 2207042


class InstagramAPIError(Exception):
    retryable = False

    def __init__(self, message, status=None, code=None, subcode=None, fbtrace_id=None, payload=None):
        super().__init__(redact(message))
        self.status, self.code, self.subcode = status, code, subcode
        self.fbtrace_id, self.payload = fbtrace_id, payload


class AuthError(InstagramAPIError):
    """Token missing, expired, or revoked. Never retried — needs a human."""


class PermissionDenied(InstagramAPIError):
    """App lacks a permission/scope or the feature is unavailable for this login type."""


class RateLimitError(InstagramAPIError):
    """App, account, or publishing rate limit. Stop this run; never hot-loop."""


class TransientError(InstagramAPIError):
    retryable = True


class MediaError(InstagramAPIError):
    """Media rejected (format, size, URL unreachable, container ERROR/EXPIRED)."""


class UnsupportedOperation(InstagramAPIError):
    """The configured login type does not offer this capability."""


def classify_error(status, body):
    err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
    code, subcode = err.get("code"), err.get("error_subcode")
    msg = err.get("error_user_msg") or err.get("message") or f"HTTP {status}"
    kw = dict(status=status, code=code, subcode=subcode, fbtrace_id=err.get("fbtrace_id"), payload=err)
    if code in AUTH_CODES or status == 401:
        return AuthError(msg, **kw)
    if code in RATE_LIMIT_CODES or subcode == PUBLISH_LIMIT_SUBCODE or status == 429:
        return RateLimitError(msg, **kw)
    if code in PERMISSION_CODES or status == 403:
        return PermissionDenied(msg, **kw)
    if code in TRANSIENT_CODES or err.get("is_transient") or (status and status >= 500):
        return TransientError(msg, **kw)
    if code == 9004 or code == 36003 or code == 36000 or (subcode and 2207000 <= subcode < 2208000):
        return MediaError(msg, **kw)
    return InstagramAPIError(msg, **kw)


def urllib_transport(method, url, params, timeout):
    data = None
    if method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except ValueError:
            body = {}
        return e.code, body
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransientError(f"network error: {e}") from None


class InstagramClient:
    """Interface. Implementations: GraphInstagramClient, DryRunClient, FakeInstagramClient."""
    login_type = "instagram"
    dry_run = False

    # account / media
    def get_account(self): raise NotImplementedError
    def list_media(self, limit=25): raise NotImplementedError
    def get_media(self, media_id): raise NotImplementedError
    # publishing
    def create_image_container(self, image_url, caption=None, is_carousel_item=False): raise NotImplementedError
    def create_reels_container(self, video_url, caption, cover_url=None, share_to_feed=True): raise NotImplementedError
    def create_carousel_container(self, children, caption): raise NotImplementedError
    def get_container_status(self, container_id): raise NotImplementedError
    def publish_container(self, container_id): raise NotImplementedError
    def get_publishing_limit(self): raise NotImplementedError
    # comments on OWN media
    def list_comments(self, media_id, limit=50): raise NotImplementedError
    def reply_to_comment(self, comment_id, message): raise NotImplementedError
    def hide_comment(self, comment_id, hide=True): raise NotImplementedError
    # insights
    def get_media_insights(self, media_id, product_type="FEED"): raise NotImplementedError
    def get_account_insights(self, since=None, until=None): raise NotImplementedError
    # discovery (Facebook Login only)
    def business_discovery(self, username, media_limit=5): raise NotImplementedError
    def hashtag_search(self, hashtag): raise NotImplementedError
    def hashtag_recent_media(self, hashtag_id, limit=10): raise NotImplementedError
    # token lifecycle
    def refresh_token(self): raise NotImplementedError
    def debug_token(self): raise NotImplementedError


class GraphInstagramClient(InstagramClient):
    def __init__(self, access_token, account_id, *, login_type="instagram", graph_version="v25.0",
                 app_id="", app_secret="", timeout=30, max_retries=3, backoff_base=2.0,
                 backoff_max=60.0, transport=None, sleep=time.sleep):
        if not access_token or not account_id:
            raise AuthError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID must be set")
        self._token = access_token
        self.account_id = account_id
        self.login_type = login_type
        self.base = f"{HOSTS[login_type]}/{graph_version}"
        self.app_id, self._app_secret = app_id, app_secret
        self.timeout, self.max_retries = timeout, max_retries
        self.backoff_base, self.backoff_max = backoff_base, backoff_max
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.calls = 0

    def __repr__(self):
        return f"GraphInstagramClient(account_id={self.account_id!r}, login_type={self.login_type!r})"

    # ---- plumbing ----
    def _auth_params(self):
        params = {"access_token": self._token}
        if self.login_type == "facebook" and self._app_secret:
            params["appsecret_proof"] = hmac.new(self._app_secret.encode(), self._token.encode(),
                                                 hashlib.sha256).hexdigest()
        return params

    def _request(self, method, path, params=None, *, retry=True, base=None):
        url = f"{base or self.base}/{path.lstrip('/')}"
        all_params = {k: v for k, v in (params or {}).items() if v is not None}
        all_params.update(self._auth_params())
        attempts = (self.max_retries + 1) if retry else 1
        last = None
        for attempt in range(attempts):
            self.calls += 1
            try:
                status, body = self.transport(method, url, all_params, self.timeout)
            except TransientError as e:
                last = e
            else:
                if status < 400 and not (isinstance(body, dict) and "error" in body):
                    return body
                last = classify_error(status, body)
                if not last.retryable:
                    raise last
            if attempt < attempts - 1:
                self.sleep(min(self.backoff_max, self.backoff_base * (2 ** attempt)))
        raise last

    def _require_facebook(self, feature):
        if self.login_type != "facebook":
            raise UnsupportedOperation(
                f"{feature} is only available with Instagram API with Facebook Login (api.login_type: facebook)")

    # ---- account / media ----
    def get_account(self):
        return self._request("GET", self.account_id,
                             {"fields": "id,username,name,followers_count,follows_count,media_count"})

    def list_media(self, limit=25):
        return self._request("GET", f"{self.account_id}/media", {"fields": MEDIA_FIELDS, "limit": limit}).get("data", [])

    def get_media(self, media_id):
        return self._request("GET", media_id, {"fields": MEDIA_FIELDS})

    # ---- publishing ----
    def create_image_container(self, image_url, caption=None, is_carousel_item=False):
        params = {"image_url": image_url}
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        else:
            params["caption"] = caption
        # Unpublished containers expire on their own, so retrying creation is harmless.
        return self._request("POST", f"{self.account_id}/media", params)["id"]

    def create_reels_container(self, video_url, caption, cover_url=None, share_to_feed=True):
        return self._request("POST", f"{self.account_id}/media", {
            "media_type": "REELS", "video_url": video_url, "caption": caption,
            "cover_url": cover_url, "share_to_feed": "true" if share_to_feed else "false"})["id"]

    def create_carousel_container(self, children, caption):
        return self._request("POST", f"{self.account_id}/media", {
            "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption})["id"]

    def get_container_status(self, container_id):
        body = self._request("GET", container_id, {"fields": "status_code,status"})
        return body.get("status_code"), body.get("status")

    def publish_container(self, container_id):
        # Not auto-retried: a lost response after a successful publish must not trigger a
        # second publish. The publisher reconciles via list_media instead.
        return self._request("POST", f"{self.account_id}/media_publish", {"creation_id": container_id},
                             retry=False)["id"]

    def get_publishing_limit(self):
        data = self._request("GET", f"{self.account_id}/content_publishing_limit",
                             {"fields": "quota_usage,config"}).get("data", [{}])
        row = data[0] if data else {}
        total = (row.get("config") or {}).get("quota_total", 100)
        return {"used": row.get("quota_usage", 0), "total": total}

    # ---- comments ----
    def list_comments(self, media_id, limit=50):
        return self._request("GET", f"{media_id}/comments", {"fields": COMMENT_FIELDS, "limit": limit}).get("data", [])

    def reply_to_comment(self, comment_id, message):
        return self._request("POST", f"{comment_id}/replies", {"message": message}, retry=False)["id"]

    def hide_comment(self, comment_id, hide=True):
        return self._request("POST", comment_id, {"hide": "true" if hide else "false"}).get("success", False)

    # ---- insights ----
    def get_media_insights(self, media_id, product_type="FEED"):
        metrics = MEDIA_METRICS["REELS" if product_type == "REELS" else "FEED"]
        try:
            body = self._request("GET", f"{media_id}/insights", {"metric": ",".join(metrics)})
        except InstagramAPIError as e:
            if e.code != 100:
                raise
            # One unsupported metric fails the whole call; fall back to the stable core set.
            body = self._request("GET", f"{media_id}/insights", {"metric": "reach,likes,comments,saved,shares"})
        return _flatten_insights(body)

    def get_account_insights(self, since=None, until=None):
        body = self._request("GET", f"{self.account_id}/insights", {
            "metric": ",".join(ACCOUNT_METRICS), "period": "day", "metric_type": "total_value",
            "since": since, "until": until})
        return _flatten_insights(body)

    # ---- discovery (Facebook Login only) ----
    def business_discovery(self, username, media_limit=5):
        self._require_facebook("Business Discovery")
        fields = (f"business_discovery.username({username}){{username,name,followers_count,media_count,"
                  f"media.limit({media_limit}){{id,caption,like_count,comments_count,permalink,timestamp,media_type}}}}")
        return self._request("GET", self.account_id, {"fields": fields}).get("business_discovery", {})

    def hashtag_search(self, hashtag):
        self._require_facebook("Hashtag Search")
        data = self._request("GET", "ig_hashtag_search", {"user_id": self.account_id, "q": hashtag}).get("data", [])
        return data[0]["id"] if data else None

    def hashtag_recent_media(self, hashtag_id, limit=10):
        self._require_facebook("Hashtag Search")
        return self._request("GET", f"{hashtag_id}/recent_media", {
            "user_id": self.account_id, "limit": limit,
            "fields": "id,caption,permalink,timestamp,like_count,comments_count,media_type"}).get("data", [])

    # ---- token lifecycle ----
    def refresh_token(self):
        """Instagram Login long-lived tokens (60 days) can be refreshed once they are >24h old.
        Returns {'access_token', 'expires_in'}; the caller is responsible for storing the new
        token in its secret store — this code never writes secrets to disk."""
        if self.login_type != "instagram":
            raise UnsupportedOperation("Facebook Login: use a System User token or re-exchange via the App Dashboard")
        body = self._request("GET", "refresh_access_token", {"grant_type": "ig_refresh_token"},
                             base=HOSTS["instagram"])
        return {"access_token": body.get("access_token"), "expires_in": body.get("expires_in")}

    def debug_token(self):
        if self.login_type != "facebook" or not (self.app_id and self._app_secret):
            return None
        body = self._request("GET", "debug_token", {"input_token": self._token},
                             base=f"{HOSTS['facebook']}")
        return body.get("data", {})


def _flatten_insights(body):
    out = {}
    for item in (body or {}).get("data", []):
        name = item.get("name")
        if "total_value" in item:
            out[name] = (item["total_value"] or {}).get("value")
        elif item.get("values"):
            out[name] = item["values"][-1].get("value")
    return out


class DryRunClient(InstagramClient):
    """Reads pass through to a real client (if any); writes are simulated, never sent."""
    dry_run = True

    def __init__(self, reader=None):
        from .fake import FakeInstagramClient
        self.reader = reader or FakeInstagramClient()
        self.login_type = getattr(self.reader, "login_type", "instagram")
        self._sim = FakeInstagramClient(login_type=self.login_type)
        self.account_id = getattr(self.reader, "account_id", "dry-run")

    def __getattr__(self, name):
        return getattr(self.reader, name)

    def get_account(self): return self.reader.get_account()
    def list_media(self, limit=25): return self.reader.list_media(limit)
    def get_media(self, media_id):
        if str(media_id).startswith("dryrun-"):
            return self._sim.get_media(media_id)
        return self.reader.get_media(media_id)
    def list_comments(self, media_id, limit=50):
        return [] if str(media_id).startswith("dryrun-") else self.reader.list_comments(media_id, limit)
    def get_media_insights(self, media_id, product_type="FEED"):
        return {} if str(media_id).startswith("dryrun-") else self.reader.get_media_insights(media_id, product_type)
    def get_account_insights(self, since=None, until=None): return self.reader.get_account_insights(since, until)
    def get_publishing_limit(self): return self.reader.get_publishing_limit()
    def business_discovery(self, username, media_limit=5): return self.reader.business_discovery(username, media_limit)
    def hashtag_search(self, hashtag): return self.reader.hashtag_search(hashtag)
    def hashtag_recent_media(self, hashtag_id, limit=10): return self.reader.hashtag_recent_media(hashtag_id, limit)
    def debug_token(self): return self.reader.debug_token()

    # simulated writes
    def create_image_container(self, image_url, caption=None, is_carousel_item=False):
        return self._sim.create_image_container(image_url, caption, is_carousel_item)
    def create_reels_container(self, video_url, caption, cover_url=None, share_to_feed=True):
        return self._sim.create_reels_container(video_url, caption, cover_url, share_to_feed)
    def create_carousel_container(self, children, caption): return self._sim.create_carousel_container(children, caption)
    def get_container_status(self, container_id): return self._sim.get_container_status(container_id)
    def publish_container(self, container_id): return "dryrun-" + self._sim.publish_container(container_id)
    def reply_to_comment(self, comment_id, message): return "dryrun-" + self._sim.reply_to_comment(comment_id, message)
    def hide_comment(self, comment_id, hide=True): return True
    def refresh_token(self): raise UnsupportedOperation("token refresh is disabled in dry-run mode")


def build_client(ctx):
    """Construct the client for a brand from config + env secrets."""
    api = ctx.config["api"]
    real = None
    if ctx.secrets.has_credentials:
        real = GraphInstagramClient(
            ctx.secrets.access_token, ctx.secrets.account_id, login_type=api["login_type"],
            graph_version=api["graph_version"], app_id=ctx.secrets.app_id, app_secret=ctx.secrets.app_secret,
            timeout=api["timeout_seconds"], max_retries=api["max_retries"],
            backoff_base=api["backoff_base_seconds"], backoff_max=api["backoff_max_seconds"])
    if api["mode"] == "live":
        if real is None:
            raise AuthError("api.mode is live but INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_ACCOUNT_ID are not set")
        return real
    return DryRunClient(real)
