---
name: social-content-build
description: Stage 4 of the social content pipeline — takes a brand and one or more user-approved clusters from an idea board and produces finished, per-platform captions, hashtags, and image-gen prompts. Only run this after the user has explicitly approved cluster(s) from a social-trend-scan idea board. Trigger on "build the content for <brand> cluster <N>", "write the posts for <theme>".
---

Produce a finished, copy-paste-ready content package for one or more already-approved clusters. Never invent or substitute a cluster the user didn't approve — if it's unclear which cluster(s) they mean, ask rather than guessing from the most recent idea board.

## Step 0 — load context

Read `brands/<slug>/social-content-system.md` and `brands/<slug>/visual-design-system.md`. Read the relevant idea board from `brands/<slug>/idea-boards/` to get the approved cluster's theme name, rationale, and per-channel angle sketch.

## Step 1 — content agents

For the approved cluster, invoke, in this order (caption first since the other two reference it):
1. `caption-writer` → per-channel captions and, where applicable, beat sheets.
2. `hashtag-strategist` → per-channel hashtag sets.
3. `image-prompt-engineer` → per-image prompts, given the captions from step 1.

These three can run in parallel once `caption-writer` has returned, since both `hashtag-strategist` and `image-prompt-engineer` only need the cluster context (and, for the image agent, the captions) rather than each other's output.

## Step 2 — consistency review

Pass the combined draft package to `brand-consistency-reviewer` along with both brand files. Apply its direct fixes; keep its flagged-for-human-judgment notes attached to the final output rather than resolving them yourself.

## Step 3 — save and present

Write the finished package to `brands/<slug>/content-packages/<YYYY-MM-DD>-<cluster-slug>.md`, grouped by channel, each with caption, hashtags, and image prompt(s) as plain, copy-paste-ready text blocks. Include the reviewer's notes section at the end. Present it in the chat and point out anything flagged for the user's judgment.
