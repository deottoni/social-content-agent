---
name: brand-onboarding
description: Interviews the user to onboard a new brand/client into the social-content-agent pipeline, producing brands/<slug>/visual-design-system.md and social-content-system.md from the templates in brands/_template/. Run this once per brand before social-trend-scan or social-content-build will work for it. Trigger on "onboard a new brand", "set up <brand> for social content", "add a client to the social pipeline".
---

Run a structured interview with the user and write the two brand files from the templates in `brands/_template/`. This is the only supported way to create a new brand folder — never hand-write these files, and never invent answers on the user's behalf. If the user doesn't know an answer yet, leave that section marked `[TBD]` rather than guessing, and say so at the end so they know what's still open.

## Step 0 — brand slug

Ask for the brand/client name and derive a filesystem-safe slug (lowercase, hyphenated). Confirm the slug with the user before creating `brands/<slug>/`. If a folder with that slug already exists, tell the user and ask whether they want to update it or pick a different slug — never silently overwrite an existing brand.

## Step 1 — visual design system

Ask: "Does this brand have a live website, or an existing design-system-teardown output already?"
- If yes and the `design-system-teardown` skill is available in this session: offer to run it against the site and use its output directly for `visual-design-system.md`, reformatted to match the template's headers if needed.
- If no (new or purely social-native brand): ask a short set of manual questions covering the template's sections at lighter detail — primary colors (names are fine, exact hex is a bonus not a requirement), font vibe if any, whether there's a recurring mascot/character (get a description detailed enough to reuse in image prompts), and general imagery style (photography vs illustration vs 3D vs flat, and the mood).

Write `brands/<slug>/visual-design-system.md` from the template, filled in with the answers.

## Step 2 — social content system

Interview the user through each section of `brands/_template/social-content-system.md`, in order. Ask these as real questions in conversation — don't dump the whole template at once. Suggested phrasing per section:

- **Audience**: "Who's this content actually for, and what do they care about? Which platforms do they actually spend time on?"
- **Voice & tone**: "Give me 3-5 words for how this brand sounds. Any lines that feel very 'on voice'? Anything the brand should never sound like?"
- **Content pillars**: "What are the 3-6 topics this brand's content keeps coming back to?" — for a brand that's clearly evergreen (comedy, mascot, meme, evergreen tips), push a bit harder here since this list becomes the primary idea source.
- **Mode & cadence**: ask directly whether this brand's content should be trend/recency-driven, evergreen, or a mix, and roughly how often they'd want to run the idea-scan (informational for now — nothing is scheduled yet).
- **Channels & post-type mix**: "Which platforms, and roughly what mix of formats on each?" (e.g. mostly Reels vs mostly carousels on IG)
- **Format rules per channel**: can often be inferred reasonably from the channel/pillars answers plus general platform norms — draft a reasonable default per channel and show it to the user to confirm/adjust rather than asking from a blank page.
- **Brand-safety no-go list**: "Anything this brand should never post about, or any disclaimer it always needs?"

Write `brands/<slug>/social-content-system.md` from the template, filled in with the answers (and reasonable stated defaults for format rules, clearly marked as defaults).

## Step 3 — confirm

Show the user both finished files (or a summary if long) and ask if anything needs correcting before considering onboarding done. Create the empty `idea-boards/` and `content-packages/` folders under `brands/<slug>/`. Remind the user they can now run `social-trend-scan` for this brand.
