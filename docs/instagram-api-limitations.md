# Instagram API — capabilities and limitations

What the Instagram operating layer can and cannot do, and why. It only uses the
**official Instagram Platform API** (Meta Graph API, pinned at `v25.0` in
`instagram_os/defaults.yaml` → `api.graph_version`).

> **Verification note (2026-09-23).** `developers.facebook.com` was not reachable from the
> environment this was built in, so the facts below come from a detailed April 2026
> reference of the official docs plus search results quoting Meta's pages. Before going
> live, check the linked Meta pages for anything that has moved — especially metric
> names, permission names, and publishing limits, which Meta revises often.
> Primary sources: [Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/),
> [Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/),
> [Insights](https://developers.facebook.com/docs/instagram-platform/insights/),
> [Hashtag Search](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-hashtag-search/).

## Two API configurations

| | Instagram API with **Instagram Login** | Instagram API with **Facebook Login** |
|---|---|---|
| Host | `graph.instagram.com` | `graph.facebook.com` |
| Needs a Facebook Page | No | Yes, linked to the IG account |
| Publish image / carousel / Reels | Yes | Yes |
| Read + reply to + hide comments on own media | Yes | Yes |
| Media + account insights | Yes | Yes |
| Business Discovery (read other professional accounts) | **No** | Yes |
| Hashtag Search | **No** | Yes (needs the *Instagram Public Content Access* feature) |
| Config | `api.login_type: instagram` | `api.login_type: facebook` |

With Instagram Login, watchlist monitoring is unavailable through the API; the system logs
that once per run and you add opportunities by hand (`instagram opportunities add`).

## What the system automates (all officially supported)

| Capability | Endpoint(s) | Module |
|---|---|---|
| Publish a single image | `POST /{ig-user-id}/media` (`image_url`) → `POST /{ig-user-id}/media_publish` | `publisher.py` |
| Publish a carousel (2–10 items) | child containers with `is_carousel_item=true` → `media_type=CAROUSEL` container → publish | `publisher.py` |
| Publish a Reel | `media_type=REELS`, `video_url`, optional `cover_url` → poll `status_code` → publish | `publisher.py` |
| Check publishing quota | `GET /{ig-user-id}/content_publishing_limit` | `publisher.py` |
| List own media | `GET /{ig-user-id}/media` | `engagement.py`, `publisher.py` (reconciliation) |
| Read comments on own media | `GET /{ig-media-id}/comments` (with `replies`) | `engagement.py` |
| Reply to a comment on own media | `POST /{ig-comment-id}/replies` | `engagement.py` |
| Hide a comment on own media | `POST /{ig-comment-id}?hide=true` | `engagement.py` (spam, opt-in) |
| Media insights | `GET /{ig-media-id}/insights` | `analytics.py` |
| Account insights / followers | `GET /{ig-user-id}/insights`, `GET /{ig-user-id}?fields=followers_count` | `analytics.py` |
| Read watched professional accounts | `GET /{ig-user-id}?fields=business_discovery.username(...)` (Facebook Login) | `opportunities.py` |
| Hashtag recent media | `GET /ig_hashtag_search` → `GET /{hashtag-id}/recent_media` (Facebook Login) | `opportunities.py` |
| Refresh a long-lived token | `GET graph.instagram.com/refresh_access_token` (Instagram Login) | `scheduler.py` `token-check` |

## Not supported by the API → never automated

These are **human opportunities**. `config.py` locks them to `human` whatever the config says.

| Action | Why it is not automated |
|---|---|
| Commenting on other accounts' posts | No endpoint. Opportunities queue suggests a comment; a person posts it. |
| Liking posts | No endpoint. |
| Following / unfollowing | No endpoint. |
| Starting a DM | Messaging API is customer-initiated only (24h window after they message first). Not used here at all. |
| Editing a published caption/media | No endpoint. Fix before publishing; the validator exists for this. |
| Scheduling a post on Instagram's side | No parameter. Scheduling is ours (queue + caps + quiet hours). |
| Licensed music on Reels | Not available via API. |
| Story stickers, polls, links; highlights; pinned posts | Not available via API. Stories publishing exists in the API but is out of scope here. |
| Reading personal (non-professional) accounts | Business Discovery only covers professional accounts; Basic Display API was retired in 2025. |
| Searching by keyword, location, or user | Only hashtag search exists. Watchlist `topics` are used for *scoring*, not searching. |
| Reading follower/following lists | Not available. |
| Browser automation / scraping | Deliberately not used anywhere, even where it would "work". |

## Limits the code enforces

| Limit | Meta value | Where enforced |
|---|---|---|
| API-published posts | 100 per rolling 24h (carousel = 1) — some accounts see 50 | `publishing.min_api_quota_remaining` stops publishing near the quota; our own `max_posts_per_day` is far lower |
| Image format | **JPEG** (PNG from `render_text_card.py` is converted automatically) | `validation.py` |
| Image size / width | ≤ 8 MB; 320–1440 px wide (wider is downscaled) | `validation.py` |
| Image aspect ratio | 4:5 (0.8) to 1.91:1 | `validation.py` |
| Carousel | 2–10 items, one shared aspect ratio | `validation.py` |
| Reels | MP4/MOV, H.264/HEVC, 3 s–15 min, ≤ 1 GB, 9:16 recommended | `validation.py` (duration/codec only if `ffprobe` is installed) |
| Caption | 2,200 chars, 30 hashtags, 20 @mentions | `validation.py` |
| Media URLs | must be publicly reachable HTTPS; the API fetches them | `media_host.py` |
| Container lifecycle | `IN_PROGRESS` → `FINISHED` / `ERROR` / `EXPIRED` (unpublished containers expire) | `publisher.py` polls, recreates expired ones |
| Hashtag search | 30 unique hashtags per account per rolling 7 days | `opportunities.py` tracks and stops at 30 |
| Business Discovery / hashtag calls | ~200 × app users per hour | low per-run caps (`max_media_per_account`, `max_hashtags_per_run`) |
| General API calls | Business Use Case limits scale with account impressions (small accounts get a low floor) | exponential backoff on transient errors; rate-limit errors stop the run instead of retrying |
| Comments page size | 50 per page | `engagement.py` reads the newest page per media each run |

## Insights metrics

Media metrics requested: `reach, likes, comments, shares, saved, views, total_interactions`
(+ `ig_reels_avg_watch_time` for Reels). If Meta rejects one metric for a media type, the
whole request fails, so the client retries once with the stable core set. `impressions`
and `plays` were retired in favour of `views` (2025) and are not requested.

Account metrics requested: `reach, views, accounts_engaged, total_interactions,
follows_and_unfollows` (period `day`, `metric_type=total_value`) plus `followers_count`
snapshots, from which follower change is computed. Constraints: data can lag up to 48 h,
queries span at most 90 days, and some follower/demographic metrics are hidden for accounts
under 100 followers.

## Known gaps in this implementation

- **Reel video is not generated.** The content brain writes beat sheets, not video. A Reel
  is publishable once a human (or an external tool) drops `reel.mp4` into the post folder.
- **Resumable upload** (`upload_type=resumable` to `rupload.facebook.com`) is not used;
  Reels go through a public `video_url` like images. Add it only if hosting large videos
  publicly becomes a problem.
- **Webhooks** for comments are not used (they need a public HTTPS endpoint). Comments are
  polled on the schedule (`*/30` by default). Replies can therefore be up to one polling
  interval late.
- **Mentions / tags** (`/{ig-user-id}/tags`, replying to @mentions) and **DMs** are not
  implemented.
- **Stories** publishing and story insights are not implemented.
- Media published outside this system (by hand) is not analyzed by the learning loop,
  because its topic/hook/CTA metadata isn't known.
