"""Pre-publish validation: Instagram's media/caption rules plus brand review flags.

Brand/voice review itself is done upstream by the `brand-consistency-reviewer` agent
during content build. This step enforces what can be checked mechanically and respects
that agent's verdict (meta.md `review: flagged`) rather than re-judging tone.
"""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import ContentType

MARKDOWN_ARTIFACTS = [r"\*\*[^*]+\*\*", r"^#{1,6}\s", r"`[^`]+`", r"^\s*[-*]\s\[.\]", r"\[[^\]]+\]\([^)]+\)"]
PLACEHOLDERS = [r"\[TBD\]", r"\bTODO\b", r"\{\{[^}]*\}\}", r"lorem ipsum", r"\[insert[^\]]*\]"]


@dataclass
class ValidationResult:
    errors: list = field(default_factory=list)      # block publishing -> REQUIRES_REVIEW
    warnings: list = field(default_factory=list)    # logged, do not block
    prepared_assets: list = field(default_factory=list)  # JPEG-converted files to upload

    @property
    def ok(self):
        return not self.errors


def validate_caption(caption, rules, blocked_terms=()):
    errors = []
    if len(caption) > rules["caption_max_chars"]:
        errors.append(f"caption is {len(caption)} chars (max {rules['caption_max_chars']})")
    hashtags = re.findall(r"(?<!\w)#\w+", caption)
    if len(hashtags) > rules["max_hashtags"]:
        errors.append(f"{len(hashtags)} hashtags (max {rules['max_hashtags']})")
    if len(set(h.lower() for h in hashtags)) != len(hashtags):
        errors.append("duplicate hashtags")
    mentions = re.findall(r"(?<![\w.])@[\w.]+", caption)
    if len(mentions) > rules["max_mentions"]:
        errors.append(f"{len(mentions)} @mentions (max {rules['max_mentions']})")
    for pat in PLACEHOLDERS:
        if re.search(pat, caption, re.I | re.M):
            errors.append(f"placeholder text left in caption ({pat})")
    for pat in MARKDOWN_ARTIFACTS:
        if re.search(pat, caption, re.M):
            errors.append("markdown formatting in caption would post literally")
            break
    lowered = caption.lower()
    for term in blocked_terms:
        if term and term.lower() in lowered:
            errors.append(f"caption contains blocked term '{term}'")
    return errors


def prepare_image(path, out_dir, rules):
    """Instagram accepts JPEG only. Convert (and downscale if too wide); return (jpeg_path, errors, ratio)."""
    from PIL import Image
    errors = []
    with Image.open(path) as im:
        w, h = im.size
        ratio = w / h
        if not rules["aspect_min"] - 0.005 <= ratio <= rules["aspect_max"] + 0.005:
            errors.append(f"{path.name}: aspect ratio {ratio:.2f} outside {rules['aspect_min']}-{rules['aspect_max']}")
        if w < rules["image_min_width"]:
            errors.append(f"{path.name}: width {w}px below minimum {rules['image_min_width']}px")
        if errors:
            return None, errors, ratio
        img = im.convert("RGB")
        if w > rules["image_max_width"]:
            img = img.resize((rules["image_max_width"], round(rules["image_max_width"] / ratio)))
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{path.stem}.jpg"
        quality = 92
        while True:
            img.save(out, "JPEG", quality=quality, optimize=True)
            if out.stat().st_size <= rules["image_max_bytes"] or quality <= 60:
                break
            quality -= 8
        if out.stat().st_size > rules["image_max_bytes"]:
            errors.append(f"{path.name}: still over {rules['image_max_bytes']} bytes after compression")
        return out, errors, ratio


def probe_video(path):
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height,codec_name:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(out.stdout)
        s = (data.get("streams") or [{}])[0]
        return {"width": s.get("width"), "height": s.get("height"), "codec": s.get("codec_name"),
                "duration": float(data.get("format", {}).get("duration", 0))}
    except (subprocess.SubprocessError, ValueError):
        return None


def validate_artifact(artifact, config):
    rules = config["validation"]
    result = ValidationResult()
    result.errors.extend(artifact.problems)
    result.errors.extend(validate_caption(artifact.caption, rules, rules.get("blocked_terms") or []))

    if rules.get("respect_review_flags") and artifact.meta.get("review", "").lower() in {"flagged", "needs-review"}:
        result.errors.append("brand-consistency-reviewer flagged this post for human judgment (meta.md review: flagged)")
    if artifact.meta.get("pillar", "").lower() == "unmapped":
        result.warnings.append("post is not mapped to a content pillar")

    out_dir = artifact.folder / "_instagram"
    if artifact.content_type in (ContentType.IMAGE, ContentType.CAROUSEL):
        if artifact.content_type == ContentType.CAROUSEL and not 2 <= len(artifact.assets) <= 10:
            result.errors.append(f"carousel needs 2-10 slides, found {len(artifact.assets)}")
        ratios = set()
        for p in artifact.assets:
            try:
                jpeg, errs, ratio = prepare_image(p, out_dir, rules)
            except Exception as e:  # corrupt / unreadable file
                result.errors.append(f"{p.name}: unreadable image ({e.__class__.__name__})")
                continue
            result.errors.extend(errs)
            ratios.add(round(ratio, 2))
            if jpeg:
                result.prepared_assets.append(jpeg)
        if len(ratios) > 1:
            result.errors.append("carousel slides must share one aspect ratio")
    elif artifact.content_type == ContentType.REELS:
        for p in artifact.assets:
            if p.suffix.lower() not in rules["video_extensions"]:
                result.errors.append(f"{p.name}: unsupported video type")
            if p.stat().st_size > rules["video_max_bytes"]:
                result.errors.append(f"{p.name}: larger than {rules['video_max_bytes']} bytes")
            info = probe_video(p)
            if info is None:
                result.warnings.append("ffprobe not available — video duration/codec not checked locally")
            else:
                if not 3 <= info["duration"] <= 900:
                    result.errors.append(f"{p.name}: duration {info['duration']:.1f}s outside 3s-15min")
                if info["codec"] not in ("h264", "hevc"):
                    result.errors.append(f"{p.name}: codec {info['codec']} (need H.264/HEVC)")
                if info["width"] and info["height"] and abs(info["width"] / info["height"] - 9 / 16) > 0.02:
                    result.warnings.append(f"{p.name}: not 9:16 — Reels may be cropped")
            result.prepared_assets.append(p)
        if artifact.cover:
            jpeg, errs, _ = prepare_image(artifact.cover, out_dir, dict(rules, aspect_min=0.5, aspect_max=1.91))
            result.errors.extend(errs)
            if jpeg:
                result.prepared_assets.append(jpeg)
    return result
