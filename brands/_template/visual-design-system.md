# Visual Design System — [Brand Name]

*Same shape as the bundled `design-system-teardown` skill's output (`.claude/skills/design-system-teardown/`). If this brand has a live site, `brand-onboarding` runs that skill and drops its output here instead of hand-writing this file. If there's no live site (a new or purely social-native brand), `brand-onboarding` asks a short set of manual questions to fill this in at lighter detail.*

## Color palette
[Hex values, named and role-tagged — primary, secondary, accent, neutral/background. Not just a swatch list — say what each color is *for*.]

## Typography
[Display/heading font, body font, any fallback stack. Weight/case conventions if notable (e.g. always uppercase headlines).]

## Logo & marks
[Logo usage rules, clear space, any recurring mascot/character with a description consistent enough to reuse in image-gen prompts.]

## Imagery style
[Photography vs illustration vs 3D vs flat-design, mood/lighting descriptors, any recurring motif. This is the section `image-prompt-engineer` leans on most.]

## Iconography
[Icon style — line weight, filled vs outline, corner radius, any icon set in use.]

## Rendering
**Method:** direct | prompt-only

[`direct` — this brand's imagery is a flat background with text over it (no photography/
illustration as the primary subject), so `scripts/render_text_card.py` can render real
PNGs from this file's confirmed background/text/accent hex values, font family, and
logo asset paths. `image-prompt-engineer`'s on-image-text/layout output feeds the
renderer directly. `prompt-only` (the default if unsure) — imagery relies on
photography/illustration/3D/a style no simple renderer can produce, so
`image-prompt-engineer` writes a prompt for an external tool instead, per usual. Only
set `direct` once the palette/typography/logo above are all confirmed, not still
placeholder/TBD.]

## Notes
[Anything else load-bearing for keeping generated assets visually on-brand.]
