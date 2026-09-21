---
name: design-system-ideation
description: Guides a from-scratch brand (no live website to tear down) through generating and choosing a real visual design system — color palette, typography, logo/mark concepts, and imagery style — as labeled visual options in a published Artifact, iterating with the user until a system is finalized. Writes the result into <brand-path>/visual-design-system.md (brand path resolved via .claude/brands.local.json) in the same shape design-system-teardown produces for brands with a live site. Used by brand-onboarding as the "no live website" path (replaces guessing colors from a text Q&A alone). Trigger on "help me design a look for this brand", "I don't have a site yet, help me pick colors/fonts/logo", or when brand-onboarding's Step 1 hits a brand with no live URL.
---

# Design System Ideation

For a brand with nothing to extract from (no live site), don't guess a single palette from a text answer and call it done — generate real, labeled visual options and let the user choose, the same way `design-system-teardown` gives a real extracted system for brands that do have a site. The deliverable is identical in shape (`visual-design-system.md`, same headers) — only how it's produced differs: chosen, not extracted.

## When this runs
Called from `brand-onboarding` Step 1 when the brand has no live website. Can also be run standalone if the user wants to redo/refresh a brand's visual system later.

## Steps

### 1. Elicit direction (lightweight, not exhaustive)
Ask for whatever the brand already knows, in plain conversation — don't dump a form:
- 3-5 mood/style words (e.g. "serious, warm, playful, minimal, bold")
- Any colors, fonts, or logo ideas already in mind, even rough ("something like a blue", "overlapping letters")
- Imagery style leaning: photography vs illustration vs 3D vs flat/graphic, and any explicit likes/dislikes (competitor accounts to avoid is great signal if the brand has done any competitive research)
- Whether a logo/mark is wanted at all, and whether it needs to double as a small watermark (common for social-first brands — check legibility at a tiny size, not just full size)

Take whatever is already written in brand docs/notes the user points to — don't re-ask for something already on the record.

### 2. Load `artifact-design` before building anything
This is a visual comparison the user will actually judge and pick from — follow that skill's guidance on token-based theming, real content over placeholder text, and light/dark handling.

### 3. Generate labeled options, not one guess
Build one Artifact (HTML) containing:
- **Color palette options** (3-4): each a complete mini-system (background/type/accent, not just a swatch), hex values labeled, named A/B/C/D. Vary along the direction the user gave, don't scatter randomly across unrelated moods.
- **Font pairing options** (3-4): headline + body, each rendered with real brand copy (the brand's actual voice/tagline if one exists, never lorem ipsum), numbered 1/2/3.
- **Logo/mark concepts** (2-4, only if a logo is wanted): built from whatever concept the user gave (a letterform idea, an initial, an abstract shape) in a clean geometric style — no mascot/character unless the brand explicitly wants one. Show each at a large size and at a tiny watermark size side by side, since legibility-when-small is the real test for a social-first brand. **Say explicitly, before showing them, that these are direction-only** (see the hard ceiling called out in Notes below) — real font glyphs (CSS text, actual Google Font characters) overlapped/recolored, never hand-drawn SVG paths pretending to be a finished letterform.
- **One sample post mockup** applying a default combination (palette A + font pairing 1 + logo concept I) so the system is judged in context, not just in isolation — this is also where watermark placement (corner mark vs. small @handle) gets tested for real.

Label everything so the user can respond with simple picks ("palette B, pairing 2, logo III").

### 4. Iterate
Take the user's picks and any adjustment requests (a hex nudge, a different logo angle, swap an accent) and republish the *same* artifact (same file path / `url`) rather than creating a new one each round — this keeps one running comparison instead of a scattered trail of links. Repeat until the user confirms a final system, including any pieces they explicitly want left `[TBD]` for later.

### 5. Write the final file
Once finalized, write `<brand-path>/visual-design-system.md` from `brands/_template/visual-design-system.md` (brand path resolved via `.claude/brands.local.json`, same as `brand-onboarding` Step 0), matching its section headers exactly (same shape `design-system-teardown` produces) — final hex values, the chosen font pairing, the logo/mark decision and where/how it's used as a watermark, imagery style, iconography, and a Notes section capturing anything explicitly deferred.

## Notes
- Don't let this become the live-site path in disguise — no scraping, no external references beyond what the user names. Everything here is generated for consideration, not extracted as fact.
- If the user already has strong opinions on everything (exact hex, exact font), skip straight to a single mockup for confirmation rather than manufacturing false choice.
- **Color and type options here are production-real** (actual hex values, actual Google Font names) — there's nothing lossy about picking them in this artifact. **A logo/mark is not** — HTML/CSS/SVG in a browser cannot produce a hand-tuned, optically-balanced final mark, no matter how many rounds of iteration it gets. State this distinction to the user up front, the first time a logo direction comes up, not after they've asked for a few rounds of polish and are still getting rough output. Iterate here only to settle a *direction* (which overlap/orientation, roughly what shape, which accent placement) — 1-2 rounds is normal, more than that is a sign the request has outgrown this tool.
- Once a direction is picked, hand off to a real tool rather than continuing to refine in-artifact: a freelance designer (Fiverr/99designs/Dribbble — brief them with the chosen direction and the brand's confirmed colors/type) for fully custom work, or an AI logo generator that outputs real vector/SVG (Looka, Brandmark.io, Logo.com) for something fast and simple. Raster output from an image model (Gemini, Midjourney, etc.) is concept art, not a final logo file, even when it looks finished — it still needs vectorizing (Illustrator Image Trace, or a cheap Fiverr "vectorize my logo" gig) before it's usable at arbitrary sizes.
- When the user brings back finished logo art from outside this pipeline, save it under `<brand-path>/assets/` (e.g. `logo-white-on-black.png` / `logo-black-on-white.png` for the two contrast variants a dark-ground brand typically needs) and point to those paths from the Logo & marks section — don't re-describe the mark in prose as if it were still a concept once real art exists.
