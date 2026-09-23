"""Turn local media files into the public HTTPS URLs the Instagram API requires.

The API fetches media from a URL; it does not accept uploads of local image files.
Which storage a brand uses is its own choice, so this is configuration, not code.
"""
import shlex
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


class MediaHostError(Exception):
    pass


class MediaHost:
    def url_for(self, path):
        raise NotImplementedError


class NoMediaHost(MediaHost):
    def url_for(self, path):
        raise MediaHostError("no media host configured (media_host.type: none) — the Instagram API needs a "
                             "public HTTPS URL for every image/video. See docs/instagram-setup.md#media-hosting")


class UrlPrefixHost(MediaHost):
    """You sync <brand-path>/content-packages/ to public storage; we compute the URL."""

    def __init__(self, base_url, content_dir, verify=True):
        if not base_url.startswith("https://"):
            raise MediaHostError("media_host.base_url must start with https://")
        self.base_url = base_url.rstrip("/")
        self.content_dir = Path(content_dir).resolve()
        self.verify = verify

    def url_for(self, path):
        rel = Path(path).resolve().relative_to(self.content_dir)
        url = f"{self.base_url}/{urllib.parse.quote(rel.as_posix())}"
        if self.verify:
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status >= 400:
                        raise MediaHostError(f"{url} returned HTTP {resp.status}")
            except OSError as e:
                raise MediaHostError(f"{url} is not reachable yet ({e}). Sync the content folder first.") from None
        return url


class CommandHost(MediaHost):
    """Runs a user-supplied upload command; `{path}` is replaced; stdout's last line is the URL."""

    def __init__(self, command):
        self.command = command

    def url_for(self, path):
        cmd = self.command.replace("{path}", shlex.quote(str(path)))
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, check=True)
        except subprocess.SubprocessError as e:
            raise MediaHostError(f"upload command failed: {e}") from None
        url = (out.stdout.strip().splitlines() or [""])[-1].strip()
        if not url.startswith("https://"):
            raise MediaHostError(f"upload command did not print an https URL (got {url[:80]!r})")
        return url


def build_media_host(ctx, verify=True):
    cfg = ctx.config["media_host"]
    if cfg["type"] == "url_prefix":
        return UrlPrefixHost(cfg["base_url"], ctx.content_dir, verify=verify)
    if cfg["type"] == "command":
        return CommandHost(cfg["command"])
    return NoMediaHost()
