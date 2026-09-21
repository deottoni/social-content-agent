---
name: brand-onboarding
description: Interviews the user to onboard a new brand/client, creating a sibling folder outside this repo (registered in .claude/brands.local.json) with visual-design-system.md and social-content-system.md written from the templates in brands/_template/. Run this once per brand before social-trend-scan or social-content-build will work for it. Trigger on "onboard a new brand", "set up <brand> for social content", "add a client to the social pipeline".
---

Run a structured interview with the user and write the two brand files from the templates in `brands/_template/`. This is the only supported way to create a new brand — never hand-write these files, and never invent answers on the user's behalf. If the user doesn't know an answer yet, leave that section marked `[TBD]` rather than guessing, and say so at the end so they know what's still open.

This repo is a public, reusable framework — a brand's real data (voice, palette, logos, finished posts) never lives inside it, even gitignored. Every brand gets its own folder outside this repo's git tree.

## Step 0 — brand slug and folder location

1. Ask for the brand/client name and derive a filesystem-safe slug (lowercase, hyphenated).
2. Ask where to create the brand's folder. Default suggestion: a sibling of this repo — `<parent-of-this-repo>/<slug>/` (find this repo's root with `git rev-parse --show-toplevel`, then use its parent directory; e.g. if this repo is at `/Users/you/Desktop/claude/social-content-agent`, suggest `/Users/you/Desktop/claude/<slug>/`). Let the user override the path if they want it elsewhere.
3. Ask whether this brand folder should be under version control: no git (default — simplest), plain local git with no remote, or a private GitHub repo (offer to run `gh repo create --private` if so). Never suggest or default to a public repo for brand data.
4. Check `.claude/brands.local.json` at this repo's root (create it as `{}` if it doesn't exist yet). If the slug is already registered, tell the user and ask whether they want to update that brand or pick a different slug — never silently overwrite an existing brand.
5. Create the folder at the resolved path and register `{"<slug>": "<absolute-path>"}` in `.claude/brands.local.json`.

## Step 1 — visual design system

Ask: "Does this brand have a live website?"
- If yes: run the bundled `design-system-teardown` skill (`.claude/skills/design-system-teardown/`) against the site and use its output directly for `visual-design-system.md`, reformatted to match the template's headers if needed.
- If no (new or purely social-native brand): run the bundled `design-system-ideation` skill (`.claude/skills/design-system-ideation/`) instead — it generates real, labeled visual options (palettes, font pairings, logo/mark concepts) in a published Artifact and iterates with the user until a system is chosen, rather than guessing a single palette from a text answer.

Either path writes `<brand-path>/visual-design-system.md` from the `brands/_template/` version.

## Step 2 — social content system

Interview the user through each section of `brands/_template/social-content-system.md`, in order. Ask these as real questions in conversation — don't dump the whole template at once. Suggested phrasing per section:

- **Audience**: "Who's this content actually for, and what do they care about? Which platforms do they actually spend time on?"
- **Voice & tone**: "Give me 3-5 words for how this brand sounds. Any lines that feel very 'on voice'? Anything the brand should never sound like?"
- **Content pillars**: "What are the 3-6 topics this brand's content keeps coming back to?" — for a brand that's clearly evergreen (comedy, mascot, meme, evergreen tips), push a bit harder here since this list becomes the primary idea source.
- **Mode & cadence**: ask directly whether this brand's content should be trend/recency-driven, evergreen, or a mix, and roughly how often they'd want to run the idea-scan (informational for now — nothing is scheduled yet).
- **Channels & post-type mix**: "Which platforms, and roughly what mix of formats on each?" (e.g. mostly Reels vs mostly carousels on IG)
- **Format rules per channel**: can often be inferred reasonably from the channel/pillars answers plus general platform norms — draft a reasonable default per channel and show it to the user to confirm/adjust rather than asking from a blank page.
- **Brand-safety no-go list**: "Anything this brand should never post about, or any disclaimer it always needs?"

Write `<brand-path>/social-content-system.md` from the template, filled in with the answers (and reasonable stated defaults for format rules, clearly marked as defaults).

## Step 3 — confirm

Show the user both finished files (or a summary if long) and ask if anything needs correcting before considering onboarding done. Create the empty `idea-boards/` and `content-packages/` folders under `<brand-path>/`. Remind the user they can now run `social-trend-scan` for this brand.
