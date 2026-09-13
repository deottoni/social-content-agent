---
name: design-system-teardown
description: Extract a real design system (colors, fonts, icon language, button/shape rules) from a live website's actual HTML/CSS and turn it into a themed reference artifact. Use whenever the user gives a URL and asks to pull its brand/design tokens, "reverse engineer" its style, build a mood board or style guide grounded in a real site, audit a competitor's or client's visual identity, or wants a demo of what Claude can do with a real company's website. Trigger even if they just paste a URL and say things like "what can we do with this site" or "extract the design system" or "steal the style for a mockup" — don't just describe the site, extract its real tokens from source.
---

# Design System Teardown

Turn a live website into a themed design-system reference — real hex codes, real fonts, real logo, real copy — pulled from its actual CSS, not guessed from a screenshot. The output is an Artifact: a themed documentation page a designer could actually use, and a good live demo of "point Claude at any site and get its brand tokens back."

## Why source over screenshots

`WebFetch` converts pages to markdown and summarizes with a small model — fine for reading content, useless for exact hex values, font-family names, or border-radius values. Always fetch the raw HTML and CSS with `curl` and read the real declarations. This is the whole trick: the output is *true*, not *plausible*.

## Steps

### 1. Fetch the real HTML and CSS

```bash
curl -s -A "Mozilla/5.0" "$URL" -o site.html
grep -o "<link[^>]*stylesheet[^>]*>" site.html   # find every stylesheet
```

Download the stylesheets that actually carry the brand's own styling — page-specific/theme CSS (e.g. Elementor `post-*.css`, a theme's `style.css`, a site's main bundle) — not framework resets or plugin boilerplate. WordPress/Elementor sites are common; the pattern generalizes to any stack.

### 2. Mine the CSS for real tokens

```bash
cat *.css | grep -oE '#[0-9a-fA-F]{3,6}' | sort | uniq -c | sort -rn   # palette, ranked by frequency
grep -oE 'font-family:[^;]+;' *.css | sort | uniq -c | sort -rn        # type system
grep -oE 'border-radius:[^;]+;' *.css | sort | uniq -c | sort -rn      # shape language (pills? cards? sharp?)
grep -oE 'box-shadow:[^;]+;' *.css | sort | uniq -c | sort -rn         # elevation — often there's NONE, which is itself a real finding
```

Frequency tells you the story: the most-used hex is the core brand color; a rare one that only shows up on buttons is usually a semantic accent (urgent/alert/promo). Look at *where* a color is used (grep for it near `.button`, `.cta`, `:hover`) to figure out what it means, not just what it is. Note explicitly when something is absent (no box-shadow anywhere is a real design decision, not a gap in your research).

Cross-check fonts against `<link>` tags for Google Fonts — confirms the family and gives you weights actually loaded.

### 3. Pull real assets, don't recreate them

Download the actual logo file and one representative photo/icon pattern (e.g. `grep -oE '<img[^>]*>' site.html` to find candidates, favoring ones with descriptive `alt` text). Embedding the real logo is what makes this feel like a teardown of *this* brand rather than a generic template — always prefer the real file over redrawing it.

### 4. Pull real copy, never lorem ipsum

Strip a few real headlines/testimonials/labels out of the HTML for use as type specimens:

```bash
python3 -c "
import re
html = open('site.html', encoding='utf-8').read()
text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S)
for m in re.findall(r'>([A-Z][^<>{2,80}]{5,80})<', text):
    print(m.strip())
" | sort -u
```

Use these verbatim in the typography section instead of generic filler — real copy is part of what makes the extraction credible.

### 5. Build the artifact

Load the `artifact-design` skill before writing anything — this is a documentation/reference treatment (polished, not a flashy landing page): a masthead naming the source URL and extraction date, then sections for Color (grouped by role, not just a swatch dump), Typography (specimens set in the real fonts using the real copy from step 4), Logo & Marks (the actual logo image, with callouts on what each part of the mark is doing), Iconography (the real icon/photo pattern embedded, plus any UI glyph language), Components (buttons/radii/shadow — recreate the actual gradients and pill shapes you found), and a short Usage Rules table that states the *semantic* rule behind the palette (e.g. "red appears exactly once, only on the emergency CTA — never decorative").

Follow the token-based light/dark theme structure from `artifact-design` — pick a neutral background tinted toward the extracted primary color rather than defaulting to plain gray.

If the request is demo/presentation-flavored, add a short closing callout translating the technique into 2-4 reusable demo ideas (e.g. "point this at a competitor's site," "feed the tokens back in to reskin a mockup") — this is often the actual point of the exercise, not just the reference doc itself.

### 6. Embed real images without blowing up your context

Base64-encoding a logo or photo produces tens of thousands of characters of text — never paste that through `Write`/`Edit` or read it back afterward. Instead:

1. Write the HTML with placeholder tokens (`{{LOGO_B64}}`, `{{ICON_B64}}`) via `Write`.
2. Run `scripts/embed_assets.py` to substitute real `data:` URIs directly on disk:
   ```bash
   python3 scripts/embed_assets.py template.html output.html LOGO_B64=logo.png ICON_B64=service-icon.png
   ```
3. Publish `output.html` with the `Artifact` tool. Never `Read` it back afterward — it's a wall of base64 you don't need in context; trust the script's success and Artifact's publish confirmation.

### 7. Publish

Title it `<Brand Name> Design System` (not a generic label), pick a favicon emoji that fits the brand, and write a one-sentence `description` naming what was actually extracted.

## Notes

- This works on any stack, not just WordPress — the grep patterns are CSS-generic. For a React/Tailwind site, check the compiled CSS bundle and any `tailwind.config` colors exposed in a `<style>` block instead of Elementor-style per-page CSS.
- If the site is behind heavy JS rendering (a SPA with no server-rendered CSS classes), `curl` alone won't see the real styles — fall back to reading computed styles via `claude-in-chrome` instead.
- Don't fabricate a palette if the CSS genuinely doesn't expose one cleanly — say so, and work from what favicons/logos/OG images reveal instead of inventing hex codes.
