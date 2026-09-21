# Social Content Agent — Harness Rules

This project is a **generic, replicable pipeline**, not a one-off tool for a single brand. It is built to onboard any brand or client the same way — including the maintainer's own accounts, which are just the first test case, not the design target. Never hardcode a specific brand's voice, palette, audience, or topics into a skill or agent file.

**Brand-specific content never lives inside this repo — not even gitignored.** This repo is a public, reusable framework; a brand's real data (voice, palette, logos, finished posts) lives in its own sibling folder outside this repo's git tree (e.g. `../<slug>/`, created by `brand-onboarding`). Every skill resolves a brand slug to its absolute folder path via `.claude/brands.local.json` at this repo's root (a gitignored, machine-local registry — `{"<slug>": "/absolute/path/to/brand-folder"}`) before reading or writing anything for that brand. `brands/_template/` is the only thing that stays inside this repo under `brands/` — it's the generic schema, not brand data.

## What this system does — and does not do

It goes from "what should we post about" to "here is the finished copy, hashtags, and image-gen prompt, ready to paste into IG/TikTok/YT Shorts or into a specialized image tool." **It never posts anything.** A human always does the actual posting. There is no publishing integration in this project, and none should be added without an explicit new request.

## The pipeline (stage-gate — never skip the gate)

1. **Onboarding** (`brand-onboarding` skill, run once per brand) → creates the brand's sibling folder and registers it in `.claude/brands.local.json`, then produces `<brand-path>/visual-design-system.md` and `<brand-path>/social-content-system.md`.
2. **Trend scan / ideation** (`social-trend-scan` skill) → reads the brand's `mode` (see below) to decide how much live research to do, clusters findings into 3–6 candidate themes, writes an idea board to `<brand-path>/idea-boards/`.
3. **Human approval gate.** Present the idea board and stop. Do not proceed to content build until the user has explicitly said which cluster(s) to pursue (and any redirection). This gate is the whole point of the system — never auto-approve, never skip it "to save a round trip."
4. **Content build** (`social-content-build` skill, only for approved clusters) → produces per-platform captions, hashtags, image-gen prompts, and (for short-form platforms) a beat sheet, written to `<brand-path>/content-packages/`.

## Mode drives Stage 2's depth

Every brand's `social-content-system.md` declares `mode: evergreen | trend-driven | hybrid`.
- `trend-driven` → `social-trend-scan` invokes the `trend-researcher` agent (real web/trend research, recency-weighted).
- `evergreen` → it invokes `topic-ideator` instead (draws from the brand's content pillars, no live research required).
- `hybrid` → both, with `topic-ideator` as the primary source and `trend-researcher` used only to check for timely hooks worth tying a pillar to.

Both paths feed the same `theme-synthesizer` agent and the same downstream Stage 4 — the mode only changes how Stage 2 sources its raw material.

## Output contracts

- **Idea board** (Stage 2 output): each cluster has a theme name, one-line rationale ("why this, why now" for trend-driven; "why this pillar, why this angle" for evergreen), and a rough per-platform angle. Numbered, so the user can approve by number.
- **Content package** (Stage 4 output): grouped by cluster, then by platform. Each platform entry has a caption, a hashtag set, an image-gen prompt (or Canva-autofill field mapping, if that path is configured for the brand), and — for TikTok/YT Shorts/Reels — a hook/body/CTA beat sheet. Plain text, copy-paste ready. No markdown formatting inside the actual copy fields (hashtags, captions) that would need stripping before pasting into an app.

## Agents

Each agent in `.claude/agents/` does one job and reads only the brand file(s) it needs (see each agent's own file for which). Run Stage 4's three content agents (`caption-writer`, `hashtag-strategist`, `image-prompt-engineer`) before `brand-consistency-reviewer`, which is the final pass and should see all three outputs together.

## Optional: phone capture without the computer being on

`inbox-drain` (paired with the separate private `content-inbox` repo's
`capture-reference` skill) lets a reference post be captured from a phone — via a
Claude Code cloud session against `content-inbox`, independent of whether this
computer is on — and staged for later. Running `inbox-drain` here pulls that repo,
resolves each staged capture to a real brand via `.claude/brands.local.json`, and
builds it into a post the same way `social-content-remix` does. `content-inbox` is
intentionally a separate, minimal, private repo — it never contains brand data itself,
only free-text-labeled raw captures — so the phone-side session never needs access to
any brand's actual folder.

## Adding a new brand

Always run the `brand-onboarding` skill rather than hand-writing brand files or creating a folder by hand. It asks where to create the brand's sibling folder (default: a sibling of this repo), whether that folder should be under git at all (default: no — never suggest a public repo for brand data), and registers the resulting path in `.claude/brands.local.json`. If you must hand-edit a brand file, keep the same section headers as `brands/_template/` so every agent's assumptions about where to find a field still hold.
