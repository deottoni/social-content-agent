---
name: social-content-build
description: Stage 4 of the social content pipeline — takes a brand and one or more user-approved clusters from an idea board and produces finished, per-platform captions, hashtags, and image-gen prompts. Only run this after the user has explicitly approved cluster(s) from a social-trend-scan idea board. Trigger on "build the content for <brand> cluster <N>", "write the posts for <theme>".
---

Produce a finished, copy-paste-ready content package for one or more already-approved clusters. Never invent or substitute a cluster the user didn't approve — if it's unclear which cluster(s) they mean, ask rather than guessing from the most recent idea board.

## Step 0 — load context

Resolve `<slug>`'s folder from `.claude/brands.local.json` at this repo's root (find the repo root with `git rev-parse --show-toplevel` if not already there). If the file doesn't exist or doesn't list `<slug>`, tell the user this brand hasn't been onboarded here yet and offer to run `brand-onboarding` first.

Read `<brand-path>/social-content-system.md` and `<brand-path>/visual-design-system.md`. Read the relevant idea board from `<brand-path>/idea-boards/` to get the approved cluster's theme name, rationale, and per-channel angle sketch.

## Step 1 — content agents

For the approved cluster, invoke, in this order (caption first since the other two reference it):
1. `caption-writer` → per-channel captions and, where applicable, beat sheets.
2. `hashtag-strategist` → per-channel hashtag sets.
3. `image-prompt-engineer` → per-image prompts, given the captions from step 1.

These three can run in parallel once `caption-writer` has returned, since both `hashtag-strategist` and `image-prompt-engineer` only need the cluster context (and, for the image agent, the captions) rather than each other's output.

## Step 2 — consistency review

Pass the combined draft package to `brand-consistency-reviewer` along with both brand files. Apply its direct fixes; keep its flagged-for-human-judgment notes attached to the final output rather than resolving them yourself.

## Step 3 — save and present

One folder per post, grouped by month — this is the standing convention for every brand, not a one-off. A single growing file per batch stops being browsable once a brand has dozens of posts; a flat folder of images stops being scannable once there are dozens of images. Folder-per-post-per-month scales to hundreds without either problem, and each post folder is self-contained enough to hand off to a human (or a future publish step) without cross-referencing anything else.

- `<brand-path>/content-packages/<YYYY-MM>/<NNN>-<post-slug>/` — one folder per post, numbered sequentially within the month (`001`, `002`, ...), reset to `001` each new month.
- Inside: `caption.txt` — the caption and hashtags together, exactly as they should be pasted into the platform, nothing else (no headers, no metadata — anything added there gets pasted into the post by mistake). Plus the image(s): `image.png` for a single-image post, `image-black.png` / `image-white.png` (or other named variants) when more than one colorway exists for the same post, `slide-1.png`, `slide-2.png`, ... for a carousel, in posting order.
- `<brand-path>/content-packages/<YYYY-MM>/_index.md` — one scannable table for the whole month: post number, name, format, colorway, pillar, an **Origin** column (`pipeline` / `remix` — see `social-content-remix` for the latter) if any post in the table came from a remix, and a **Status** column (`ready` / `posted`) the human updates by hand as they actually post things. This is the calendar view — read this before opening any post folder, don't rebuild it by listing directories.

Check `visual-design-system.md`'s `## Rendering` → `Method` before deciding how the image half of each post folder gets filled:
- **`direct`:** call this repo's `scripts/render_text_card.py` (this repo, not the brand folder — run it from/with a path back to this repo's root) once per image needed (once per slide for a carousel, once per colorway when a post wants both), passing the brand's confirmed hex values, font family, and the logo asset matching the requested colorway from `<brand-path>/assets/` — using `image-prompt-engineer`'s on-image-text/layout output as the headline input. Check the brand file's `## Rendering` notes for its confirmed accent treatment (e.g. a standalone `--bar`, no label text) before defaulting to `--eyebrow` — don't silently reintroduce a label the brand has explicitly moved away from. Real files (`image.png` / `image-black.png` + `image-white.png` / `slide-1.png`...) land straight in the post folder; no prompt file alongside them.
- **`prompt-only` (or the field is missing — no image-generation tool is wired into this project by default, see `image-prompt-engineer`):** the default output is a prompt, not a rendered file. If the user separately generates the image with an external tool and hands it back, put the real file(s) straight into the post folder and drop the prompts-only version once real art exists for that post.
