#!/usr/bin/env python3
"""Render a flat-background text card (quote card / carousel slide) from a brand's
confirmed visual-design-system.md tokens. Used by social-content-remix and
social-content-build when a brand's visual-design-system.md declares
`## Rendering` -> `Method: direct`. See scripts/README.md for the CLI contract.
"""
import argparse
import os
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
FONT_CACHE_DIR = SCRIPT_DIR / ".font-cache"

FONT_CSS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def ensure_font(family, weight="400"):
    """Download and cache a Google Font .ttf by family name; return the local path."""
    FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = FONT_CACHE_DIR / f"{slugify(family)}-{weight}.ttf"
    if cache_path.exists():
        return cache_path

    family_param = family.replace(" ", "+")
    css_url = f"https://fonts.googleapis.com/css2?family={family_param}:wght@{weight}&display=swap"
    req = urllib.request.Request(css_url, headers={"User-Agent": FONT_CSS_UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        css = resp.read().decode("utf-8")

    match = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)
    if not match:
        raise RuntimeError(
            f"Could not resolve a .ttf URL for font family '{family}' (weight {weight}). "
            "Google Fonts may not serve this family/weight as TrueType, or the family "
            "name doesn't match exactly. Response snippet:\n" + css[:300]
        )
    ttf_url = match.group(1)

    req = urllib.request.Request(ttf_url, headers={"User-Agent": FONT_CSS_UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        cache_path.write_bytes(resp.read())
    return cache_path


def wrap_text_to_width(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_headline(draw, text, font_path, max_width, max_height, max_size=96, min_size=36):
    """Shrink font size until the wrapped headline block fits within max_width x max_height."""
    for size in range(max_size, min_size - 1, -4):
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap_text_to_width(draw, text, font, max_width)
        line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        line_spacing = int(line_height * 1.35)
        block_height = line_spacing * len(lines)
        if block_height <= max_height:
            return font, lines, line_spacing
    # fall back to the smallest size even if it overflows slightly, rather than erroring
    font = ImageFont.truetype(str(font_path), min_size)
    lines = wrap_text_to_width(draw, text, font, max_width)
    line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    return font, lines, int(line_height * 1.35)


def paste_logo(image, logo_path, position, margin_ratio=0.04, width_ratio=0.1):
    logo = Image.open(logo_path).convert("RGBA")
    target_w = int(image.width * width_ratio)
    scale = target_w / logo.width
    logo = logo.resize((target_w, int(logo.height * scale)), Image.LANCZOS)

    margin = int(image.width * margin_ratio)
    positions = {
        "bottom-right": (image.width - logo.width - margin, image.height - logo.height - margin),
        "bottom-left": (margin, image.height - logo.height - margin),
        "top-right": (image.width - logo.width - margin, margin),
        "top-left": (margin, margin),
    }
    xy = positions.get(position, positions["bottom-right"])
    image.paste(logo, xy, logo)


def render(args):
    width, height = args.width, args.height
    margin = int(width * 0.1)
    max_text_width = width - 2 * margin

    image = Image.new("RGB", (width, height), args.bg)
    draw = ImageDraw.Draw(image)

    headline_font_path = ensure_font(args.font_family)

    # Optional eyebrow LABEL (short text above the headline) — independent of the
    # accent bar below. Most posts use neither or just the bar; the label is for
    # cases that genuinely need a visible pillar tag as text, not the default.
    eyebrow_font = None
    eyebrow_block_height = 0
    if args.eyebrow:
        eyebrow_font_path = ensure_font(args.font_family)
        eyebrow_font = ImageFont.truetype(str(eyebrow_font_path), max(24, int(args.max_size * 0.28)))
        bbox = eyebrow_font.getbbox("Ag")
        eyebrow_block_height = int((bbox[3] - bbox[1]) * 2.2)

    # Accent bar — a short standalone rule, never colored label text. Per the
    # brand's documented accent usage ("a stat-card number, an underline, a small
    # highlight"). Sits directly above or below the headline block; no label
    # required.
    bar_height = max(3, int(width * 0.003))
    bar_width = int(width * 0.14)
    bar_gap = int(width * 0.035)
    bar_block_height = (bar_height + bar_gap) if args.bar in ("above", "below") else 0

    max_text_height = height - 2 * margin - eyebrow_block_height - bar_block_height
    font, lines, line_spacing = fit_headline(
        draw, args.headline, headline_font_path, max_text_width, max_text_height,
        max_size=args.max_size, min_size=args.min_size,
    )

    block_height = line_spacing * len(lines) + eyebrow_block_height + bar_block_height
    y = (height - block_height) // 2

    if args.eyebrow:
        eyebrow_w = draw.textlength(args.eyebrow, font=eyebrow_font)
        draw.text(((width - eyebrow_w) / 2, y), args.eyebrow, font=eyebrow_font, fill=args.fg)
        y += eyebrow_block_height

    def draw_bar(top_y):
        bar_x0 = (width - bar_width) / 2
        draw.rectangle([bar_x0, top_y, bar_x0 + bar_width, top_y + bar_height], fill=args.accent or args.fg)

    if args.bar == "above":
        draw_bar(y)
        y += bar_height + bar_gap

    font_bbox = font.getbbox("Ag")
    font_line_height = font_bbox[3] - font_bbox[1]
    last_line_top = y
    for line in lines:
        line_w = draw.textlength(line, font=font)
        draw.text(((width - line_w) / 2, y), line, font=font, fill=args.fg)
        last_line_top = y
        y += line_spacing

    if args.bar == "below":
        draw_bar(last_line_top + font_line_height + bar_gap)

    if args.logo:
        paste_logo(image, args.logo, args.logo_position)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG")
    print(f"Wrote {out_path} ({width}x{height})")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bg", required=True, help='Background hex, e.g. "#0B0B0C"')
    p.add_argument("--fg", required=True, help='Headline text color hex, e.g. "#FFFFFF"')
    p.add_argument("--accent", default=None, help="Accent hex for --bar/--eyebrow (defaults to --fg if omitted)")
    p.add_argument("--font-family", required=True, help='Google Fonts family name, e.g. "Special Elite"')
    p.add_argument("--headline", required=True)
    p.add_argument("--eyebrow", default=None,
                    help="Optional short text label above the headline (rendered in --fg, not --accent). "
                         "Independent of --bar; most posts use --bar alone with no label.")
    p.add_argument("--bar", default="none", choices=["none", "above", "below"],
                    help="A short standalone accent-colored rule (no text) directly above/below the headline.")
    p.add_argument("--logo", default=None, help="Path to a transparent-background logo PNG")
    p.add_argument("--logo-position", default="bottom-right",
                    choices=["bottom-right", "bottom-left", "top-right", "top-left"])
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1350)
    p.add_argument("--max-size", type=int, default=96, help="Largest headline font size to try")
    p.add_argument("--min-size", type=int, default=36, help="Smallest headline font size to try")
    p.add_argument("--out", required=True)
    return p.parse_args()


if __name__ == "__main__":
    try:
        render(parse_args())
    except Exception as exc:
        print(f"render_text_card.py failed: {exc}", file=sys.stderr)
        sys.exit(1)
