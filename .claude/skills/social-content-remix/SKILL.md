---
name: social-content-remix
description: Ad-hoc remix of a reference post the user sends (an attached image, a description, a link, or their own draft) into a specific brand's voice and visual system — bypasses the ideation/approval stages since the user supplies the concrete reference directly. Reuses the Stage 4 content agents (caption-writer, hashtag-strategist, image-prompt-engineer, brand-consistency-reviewer) against a synthesized mini-cluster instead of an idea-board cluster. Saves into the same <brand-path>/content-packages/ convention as social-content-build (brand path resolved via .claude/brands.local.json), plus a source/ snapshot of the reference and a meta.md tagging origin and pillar mapping for later review by brand-drift-audit. Presents the finished caption and image spec directly in the chat response so it works from a mobile session. Trigger on "brand this post", "remix this in our style", "make this on-brand for <brand>", or whenever the user sends/attaches a reference post and asks for an immediate on-brand version.
---

Take one reference post the user hands you directly and return it re-skinned in a
brand's voice and visual system — right away, no idea board, no approval gate. This is
the same shape as Stage 4 (build from something already decided), not Stage 2/3
(speculative ideation) — the user supplying a concrete reference *is* the decision.

Never literally clone the reference's exact copy or image. Extract the structural/
thematic idea — format, core message/hook, tone — and write a new, on-brand post
inspired by it, not a copy of it.

## Step 0 — load context

Resolve `<slug>`'s folder from `.claude/brands.local.json` at this repo's root (find
the repo root with `git rev-parse --show-toplevel` if not already there). If the file
doesn't exist, doesn't list `<slug>`, or `<slug>` wasn't given and more than one brand
is registered, ask which brand rather than guessing.

Read `<brand-path>/social-content-system.md` and `<brand-path>/visual-design-system.md`.

## Step 1 — ingest the reference

Capture what the user actually sent: the reference image and/or description, any
original caption/text alongside it, and their note (if any) on what they like about it.
If they only sent an image with no comment, that's fine — the image itself is the
reference.

## Step 2 — synthesize a mini-cluster inline

No idea board, no new agent — build this directly, same shape `theme-synthesizer`
would hand to Stage 4: a theme name, one-line rationale, and a per-platform angle.

Also map it to one of the brand's declared content pillars (from
`social-content-system.md`). If it doesn't cleanly fit one, say so explicitly —
`pillar: unmapped` or `pillar: <closest guess> (low confidence)` — rather than forcing
a fit. This is a heads-up for later, not a block: keep going either way. This flag is
what makes `brand-drift-audit` useful once several remixes have accumulated.

## Step 3 — content agents

Same as `social-content-build` Step 1, using the Step 2 mini-cluster in place of an
idea-board cluster:
1. `caption-writer` → caption(s) and, where applicable, a beat sheet.
2. `hashtag-strategist` and `image-prompt-engineer` (parallel, once captions exist).
3. `brand-consistency-reviewer` → pass the combined draft plus both brand files. Apply
   its direct fixes; keep flagged-for-human-judgment notes attached to the output.

## Step 4 — save

Extends the existing `social-content-build` convention — do not fork a separate
folder tree:
- `<brand-path>/content-packages/<YYYY-MM>/<NNN>-<post-slug>/` — same location and
  sequential numbering as every other post. Inside: `caption.txt` (caption + hashtags,
  paste-as-is, nothing else), plus the image(s). Check `visual-design-system.md`'s
  `## Rendering` → `Method`:
  - **`direct`:** call this repo's `scripts/render_text_card.py` (this framework repo,
    not the brand folder) once per image needed (once per slide for a carousel, once
    per colorway if both are wanted), passing the brand's confirmed hex values, font
    family, and the matching logo asset from `<brand-path>/assets/` — using
    `image-prompt-engineer`'s on-image-text/layout output as the headline input. Check
    the brand file's `## Rendering` notes for its confirmed accent treatment before
    reaching for `--eyebrow` — for this brand, accent is a standalone `--bar`, no label
    text, and that default should not silently regress back to an eyebrow label. Real
    files (`image.png` / `image-black.png` + `image-white.png` / `slide-1.png`...) land
    straight in the post folder — this is what makes "deliver the actual image, not a
    prompt" work from a phone session.
  - **`prompt-only` (or the field is missing):** no image-generation tool is wired
    into this project by default (see `image-prompt-engineer`), so the output is
    `image-prompt.txt` — the full prompt plus the exact on-image text/layout — rather
    than a rendered file. If the user separately generates the image and hands it
    back, drop it into this folder as `image.png` (or the usual colorway/slide naming)
    and it supersedes the prompt file.
- New `source/` subfolder inside that same post folder: the reference exactly as given
  — the saved image if one was attached, and/or a `reference.md` with what the user
  described/sent plus their note, and the date. This is the record `brand-drift-audit`
  reads later.
- New `meta.md` inside that same post folder:
  ```
  origin: remix
  pillar: <name or "unmapped">
  confidence: <high|low>
  date: <YYYY-MM-DD>
  ```
- `<brand-path>/content-packages/<YYYY-MM>/_index.md` — add this post's row as usual,
  plus an **Origin** column (`pipeline` / `remix`) if the table doesn't have one yet.
  One calendar view for everything ready to post, not a second competing index.

## Step 5 — present in-chat

This is the actual deliverable when triggered from a phone session — put it directly
in your response, not just in the saved files:
- The finished caption + hashtags, as plain copy-paste text.
- The image itself when `Method: direct` rendered one — attach/display the actual PNG
  file(s) from Step 4 in the response, not just a path. Otherwise (`prompt-only`), the
  full prompt and exact on-image text/layout from Step 3.
- One line noting Step 2's pillar mapping if it was low-confidence or unmapped.
