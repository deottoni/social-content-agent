"""GraphInstagramClient against a mocked transport: no network, no real account."""
import pytest

from instagram_os.activity import redact
from instagram_os.client import (AuthError, GraphInstagramClient, MediaError, PermissionDenied, RateLimitError,
                                 TransientError, UnsupportedOperation, classify_error)


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, params, timeout):
        self.requests.append((method, url, dict(params)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses, **kw):
    t = Transport(responses)
    sleeps = []
    c = GraphInstagramClient("SECRET-TOKEN", "1784", transport=t, sleep=sleeps.append, backoff_base=1, **kw)
    return c, t, sleeps


def err(code, subcode=None, status=400, **extra):
    return status, {"error": dict(message="boom", code=code, error_subcode=subcode, **extra)}


def test_publish_flow_requests():
    c, t, _ = client([(200, {"id": "c1"}), (200, {"status_code": "FINISHED"}), (200, {"id": "m1"})])
    assert c.create_image_container("https://x/a.jpg", "cap") == "c1"
    assert c.get_container_status("c1")[0] == "FINISHED"
    assert c.publish_container("c1") == "m1"
    method, url, params = t.requests[2]
    assert method == "POST" and url.endswith("/v25.0/1784/media_publish") and params["creation_id"] == "c1"
    assert url.startswith("https://graph.instagram.com/")


def test_facebook_login_uses_graph_facebook_and_appsecret_proof():
    c, t, _ = client([(200, {"id": "1784", "username": "b"})], login_type="facebook", app_secret="s")
    c.get_account()
    _, url, params = t.requests[0]
    assert url.startswith("https://graph.facebook.com/") and len(params["appsecret_proof"]) == 64


@pytest.mark.parametrize("response,exc", [
    (err(190), AuthError), ((401, {}), AuthError),
    (err(4), RateLimitError), (err(32), RateLimitError), (err(9, 2207042), RateLimitError), ((429, {}), RateLimitError),
    (err(10), PermissionDenied), (err(200), PermissionDenied),
    (err(2, status=500), TransientError), (err(1, is_transient=True), TransientError),
    (err(9004), MediaError), (err(36003), MediaError),
])
def test_error_classification(response, exc):
    assert isinstance(classify_error(*response), exc)


def test_transient_errors_back_off_exponentially_then_give_up():
    c, t, sleeps = client([err(2, status=503)] * 4, max_retries=3)
    with pytest.raises(TransientError):
        c.get_account()
    assert len(t.requests) == 4 and sleeps == [1, 2, 4]


def test_backoff_is_capped():
    c, _, sleeps = client([err(2, status=503)] * 5, max_retries=4, backoff_max=3)
    with pytest.raises(TransientError):
        c.get_account()
    assert max(sleeps) == 3


def test_transient_then_success():
    c, t, _ = client([TransientError("reset"), (200, {"id": "1784"})])
    assert c.get_account()["id"] == "1784"


def test_auth_and_rate_limit_errors_are_not_retried():
    c, t, _ = client([err(190)])
    with pytest.raises(AuthError):
        c.get_account()
    c, t, _ = client([err(4)])
    with pytest.raises(RateLimitError):
        c.get_account()
    assert len(t.requests) == 1


def test_publish_and_reply_are_never_auto_retried():
    c, t, _ = client([err(2, status=503)])
    with pytest.raises(TransientError):
        c.publish_container("c1")
    c2, t2, _ = client([err(2, status=503)])
    with pytest.raises(TransientError):
        c2.reply_to_comment("x", "hi")
    assert len(t.requests) == 1 and len(t2.requests) == 1


def test_missing_credentials():
    with pytest.raises(AuthError):
        GraphInstagramClient("", "1784")


def test_discovery_requires_facebook_login():
    c, _, _ = client([])
    with pytest.raises(UnsupportedOperation):
        c.business_discovery("acme")
    with pytest.raises(UnsupportedOperation):
        c.hashtag_search("ai")


def test_insights_fallback_when_a_metric_is_unsupported():
    c, t, _ = client([err(100), (200, {"data": [{"name": "reach", "values": [{"value": 10}]},
                                                {"name": "likes", "total_value": {"value": 3}}]})])
    assert c.get_media_insights("m1") == {"reach": 10, "likes": 3}


def test_token_never_leaks_into_repr_or_errors():
    c, _, _ = client([(400, {"error": {"message": "bad access_token=SECRET-TOKEN&x=1", "code": 100}})])
    assert "SECRET" not in repr(c)
    with pytest.raises(Exception) as e:
        c.get_account()
    assert "SECRET" not in str(e.value)
    assert redact("Bearer abc.def") == "Bearer ***"


def test_secrets_repr_hides_token():
    from instagram_os.config import Secrets
    s = Secrets.from_env({"INSTAGRAM_ACCESS_TOKEN": "IGQVJ-xyz", "INSTAGRAM_ACCOUNT_ID": "1", "META_APP_SECRET": "s3cr3t"})
    assert "IGQVJ" not in repr(s) and "s3cr3t" not in repr(s)
