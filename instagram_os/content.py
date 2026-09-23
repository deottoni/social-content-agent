"""Read finished posts from the existing content-package convention.

`social-content-build` and `social-content-remix` write one folder per post:
    <brand-path>/content-packages/<YYYY-MM>/<NNN>-<slug>/
        caption.txt                      paste-ready caption + hashtags
        image.png | image-<variant>.png  single image (one or more colorways)
        slide-1.png, slide-2.png, ...    carousel, in posting order
        reel.mp4 | video.mp4 (+ cover.jpg)  Reel (added for the Instagram layer)
        meta.md                          optional `key: value` metadata
and a month calendar `_index.md` with a Status column (ready / posted).

This module only reads that convention (and flips Status to `posted` after a real
publish). It never writes captions or media.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .models import ContentType

IMAGE_EXTS = (".png", ".jpg", ".jpeg")
VIDEO_NAMES = ("reel", "video")


@dataclass
class ContentArtifact:
    content_id: str
    folder: Path
    content_type: ContentType
    caption: str
    assets: list
    cover: Path = None
    meta: dict = field(default_factory=dict)
    index_status: str = None
    problems: list = field(default_factory=list)   # reasons it cannot be published as-is

    @property
    def content_hash(self):
        h = hashlib.sha256(self.caption.encode())
        for p in self.assets:
            h.update(p.name.encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
        return h.hexdigest()

    @property
    def hook(self):
        if self.meta.get("hook"):
            return self.meta["hook"]
        first = next((l.strip() for l in self.caption.splitlines() if l.strip()), "")
        return first[:150]

    @property
    def cta(self):
        return self.meta.get("cta") or detect_cta(self.caption)

    @property
    def topic(self):
        return self.meta.get("topic") or self.meta.get("pillar") or self.folder.name.split("-", 1)[-1]


CTA_PATTERNS = [r"\bsave (this|it)\b", r"\bshare\b", r"\bcomment\b", r"\bfollow\b", r"\blink in bio\b",
                r"\btag\b", r"\bdm\b", r"\bsend (this|it)\b", r"\?\s*$"]


def detect_cta(caption):
    body = "\n".join(l for l in caption.splitlines() if not l.strip().startswith("#"))
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    for line in reversed(lines[-3:]):
        for pat in CTA_PATTERNS:
            if re.search(pat, line, re.I):
                return line[:150]
    return None


def parse_meta(path):
    meta = {}
    if not path.exists():
        return meta
    for line in path.read_text().splitlines():
        line = line.strip().strip("`")
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
    return meta


def parse_index(path):
    """Return {post_number: status} from a month's _index.md markdown table."""
    statuses = {}
    if not path.exists():
        return statuses
    header = None
    for line in path.read_text().splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        row = dict(zip(header, cells))
        num = re.sub(r"\D", "", row.get("#", row.get("post", row.get("no", cells[0]))))
        status = row.get("status", "").lower()
        if num:
            statuses[num.zfill(3)] = re.sub(r"[^a-z]", "", status)
    return statuses


def mark_posted_in_index(month_dir, post_number):
    """Flip the Status cell for one post to `posted` (the human-maintained calendar)."""
    path = Path(month_dir) / "_index.md"
    if not path.exists():
        return False
    lines = path.read_text().splitlines()
    header, changed = None, False
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if "status" not in header or all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        if re.sub(r"\D", "", cells[0]).zfill(3) == post_number:
            cells[header.index("status")] = "posted"
            lines[i] = "| " + " | ".join(cells) + " |"
            changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n")
    return changed


def _month_dirs(content_dir, months_back):
    today = date.today()
    wanted = set()
    y, m = today.year, today.month
    for _ in range(max(1, months_back)):
        wanted.add(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return sorted(d for d in Path(content_dir).glob("*-*") if d.is_dir() and d.name in wanted)


def load_post(folder, index_status=None):
    folder = Path(folder)
    content_id = f"{folder.parent.name}/{folder.name}"
    meta = parse_meta(folder / "meta.md")
    caption_path = folder / "caption.txt"
    caption = caption_path.read_text().strip() if caption_path.exists() else ""
    problems = []
    if not caption:
        problems.append("caption.txt missing or empty")

    files = sorted(p for p in folder.iterdir() if p.is_file())
    videos = [p for p in files if p.stem.lower() in VIDEO_NAMES and p.suffix.lower() in (".mp4", ".mov")]
    slides = sorted((p for p in files if re.fullmatch(r"slide-\d+", p.stem.lower()) and p.suffix.lower() in IMAGE_EXTS),
                    key=lambda p: int(p.stem.split("-")[1]))
    images = [p for p in files if re.fullmatch(r"image(-[\w-]+)?", p.stem.lower()) and p.suffix.lower() in IMAGE_EXTS]
    cover = next((p for p in files if p.stem.lower() == "cover" and p.suffix.lower() in IMAGE_EXTS), None)

    if videos:
        ctype, assets = ContentType.REELS, videos[:1]
    elif slides:
        ctype, assets = ContentType.CAROUSEL, slides
    elif images:
        ctype = ContentType.IMAGE
        variant = meta.get("variant") or meta.get("colorway")
        if len(images) == 1:
            assets = images
        elif variant:
            assets = [p for p in images if p.stem.lower() == f"image-{variant.lower()}"]
            if not assets:
                problems.append(f"meta.md variant '{variant}' does not match any image-*.png")
        else:
            assets = []
            problems.append(f"{len(images)} colorway variants and no `variant:` in meta.md — pick one")
    else:
        ctype, assets = ContentType.IMAGE, []
        if (folder / "image-prompt.txt").exists():
            problems.append("only image-prompt.txt exists — render or drop in the real image first")
        else:
            problems.append("no publishable media (image.png, slide-N.png, reel.mp4)")

    return ContentArtifact(content_id=content_id, folder=folder, content_type=ctype, caption=caption,
                           assets=assets, cover=cover, meta=meta, index_status=index_status, problems=problems)


def discover_ready_posts(content_dir, months_back=2):
    """Posts a human has marked ready (in _index.md or meta.md `status: ready`).
    Anything marked `posted` is skipped: it was already posted, by hand or by us."""
    out = []
    for month in _month_dirs(content_dir, months_back):
        index = parse_index(month / "_index.md")
        for folder in sorted(p for p in month.iterdir() if p.is_dir() and re.match(r"\d{3}-", p.name)):
            num = folder.name[:3]
            meta_status = parse_meta(folder / "meta.md").get("status", "").lower()
            if "posted" in (index.get(num), meta_status):
                continue
            status = index.get(num) or meta_status
            if status == "ready":
                out.append(load_post(folder, index_status=status))
    return out
