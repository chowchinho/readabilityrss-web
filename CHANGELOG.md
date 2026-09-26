# Changelog

Notable changes to ReadabilityRSS, newest first. Releases are named by date. The
dashboard shows the build time in its top bar.

## 2026-09-26

### Added
- **Read-to-the-end signal.** The reader sends a `read_complete` event once per
  article when you have reached the paragraph at 80% of the text and spent at least
  half the estimated reading time on it (15 to 90 seconds, counted only while the
  tab is visible). Position is measured on the text rather than on scroll pixels,
  so a lead image or a gallery at the end does not skew it, and a translated article
  that shows the original text counts only the translation toward reading time.
  The ranking treats it as half an upvote (`signal_read_complete`, default 0.5). An
  article you voted on ignores it, so reading to the end and upvoting counts once.
- `GET /api/reader/ranking/scores` returns the axis multipliers under `factors`, so
  a client that rebuilds the score breakdown can make its rows add up.

### Changed
- **Region is now a modifier rather than a peer of topic.** Its weight is
  multiplied by 0.3 (`REGION_FACTOR`). Every vote lands on one of only a handful of
  regions, so the most-shown region used to saturate first and push well-liked
  topics from other regions far down the page.
- The smart-sort re-ranker balances region alongside feed, topic and type, using
  the scaled region weights.

## 2026-09-25

### Added
- **Translation status report** at the top of the dashboard's Translation tab. It
  shows articles translated, success rate with fallback and failure counts, the
  live queue by feed (refreshed every 30 seconds), average and maximum wait per
  provider, cost per provider, and an arrived-versus-translated chart. The window
  can be set to 1 hour, 6 hours, 12 hours, 24 hours, 3 days or 7 days. The data
  comes from `GET /api/translation/status?hours=N`.
- The phone app can start an on-demand translation. Session validation is now
  shared by the auth middleware and the reader routes.

### Changed
- AI features and the label weights (topics, regions, types) have their own **AI**
  tab. The Translation tab now holds the translation settings only.
- Dashboard tab icons are monotone inline SVGs that follow the theme colour, in
  place of mixed emoji.
- The feed list gives the local model its own translator badge. It previously
  showed the Qwen logo.
- A feed set to the local model translates its title during the refresh and
  hands the body to the background worker, instead of timing out on the
  per-article budget.

### Fixed
- The local model's output is cleaned before it is stored. Inline markup no
  longer leaks into the text as visible tags, and stray Simplified characters are
  converted to Traditional.
- When the local model server is down, queued articles stay queued. They were
  previously dropped from the queue and never translated.
- A feed on the local model no longer shows a badge claiming it fell back from
  itself.
- `LMT_URL` no longer has a hard-coded default. The local model is off unless you
  set it.

## 2026-09-23

### Added
- Four translation providers, selectable per feed: Qwen-MT-flash (default),
  DeepSeek, the free Google endpoint, and an optional local model (LMT-60) on your
  own hardware. The local model backs up whichever provider a feed uses. See
  `docs/local-translation.md`.
- On-demand translation of the open article in the reader. It streams block by
  block.
- A Simplified-to-Traditional conversion pass with an extensible Hong Kong
  vocabulary glossary, editable from the dashboard.
- Cost tracking per provider, with scheduled and on-demand usage shown separately.

### Changed
- The free Google endpoint paces its own requests and backs off when throttled
  instead of retrying. It is documented as best-effort.

### Fixed
- A failed translation no longer damages the article. It used to append
  "(Translation Error)" to the stored title and body, which also reached URL
  slugs. Failures now leave the source untouched and re-queue the article.

## 2026-09-08

### Added
- An app icon for both web apps: favicon set, apple-touch icon and maskable icon.
  It replaces the icon-font glyph that served as the logo.

### Changed
- The single-feed header's "Mark Feed Read" row is replaced by a two-step
  **Read All** pill in the feed banner (click once to arm, again to confirm). The
  pill is disabled while offline.

## 2026-09-03

Initial public release.

- Full-article extraction with lazy-image recovery, served as a reader UI,
  full-text RSS, or both.
- One container: a single FastAPI process serves the reader at `/`, the
  management dashboard at `/manage/`, and the API.
- Published on loopback by default. `BIND_ADDR` and `HOST_PORT` expose it to a LAN
  or move it off port 8001.
- Ranking starts neutral: every topic, region and type weight starts at zero and
  learns from your votes.
- First-run account setup in the reader.
- The data directory, CORS origins and allowed hosts are configurable.
- Runtime and test dependencies are split, which keeps the Docker image lean.
- Fixed database worker threads that outlived their connections and stopped the
  process from exiting.

---

# Before the public release

The project was developed privately from March 2026. These entries summarise that
history by month. They have no matching commits in this repository, which starts
at the 2026-09-03 release.

## 2026-08

### Added
- **Sources Index**, a magazine-style landing view for the reader, with category
  pills that follow your custom category order.
- **Personalised feed ranking.** An LLM tags each article by topic, region and
  type, and the reader records opens, hover dwell and impressions. A feed-order
  setting chooses between personalised, latest and random.
- **Vote-driven ranking.** Thumbs up and down on every card layout. Weights decay
  over time and saturate, and a re-ranker keeps topic and type diversity. An info
  popover shows the full score arithmetic, and a dashboard table shows what
  voting has changed. Exposure decay and exploration slots keep labels you rarely
  see from disappearing.
- An **AI master switch** that turns off tagging, ranking and the feedback
  controls together.
- A ranking API for the Android app.
- **Smart cropping** for card thumbnails. Focal points come from face and object
  detection and are computed on the server, then served in bulk.
- An **autoplay image slideshow** on article cards. It runs only while a card is
  on screen.
- **Single-origin serving.** One process serves the reader at `/`, the dashboard
  at `/manage/` and the API, and both apps share one sign-in.
- A global system settings page in the dashboard, with storage indicators.
- Card preview snippets are stored with each article instead of being rebuilt on
  every page load.
- A subtle mark on cards you have voted on.

### Changed
- Article typography redesigned for bilingual (original plus translation)
  reading.
- SVG images are dropped as site chrome, and images are no longer sent through
  the translator.
- The service worker is scoped to reader paths and is not cached by CDNs.

### Fixed
- Deleting a feed no longer leaves unreachable articles behind.
- Category views on the index no longer come up empty. They paginate per
  category.
- The reader no longer bypasses the image cache through `srcset` and `<picture>`.
- Tagging no longer freezes the server during a backfill, or runs before the
  article body is parsed.
- Keyboard navigation no longer counts every article stepped past as read.
- Votes survive refreshes, offline use and navigating back.

## 2026-07

### Fixed
- Google Translate requests are batched, so refreshing a feed no longer times out.

## 2026-05

### Added
- Per-feed translation with DeepSeek or Google, with a provider badge on each
  feed and translation cost logging (a 7-day cost badge per feed).
- A **negative keywords** filter that drops articles by title.
- Category reordering in the reader.
- A reader setting that hides feeds with no unread articles.
- A per-feed option to use the parsing date as the publish date.
- The dashboard shows each feed's latest article date as a relative badge (Today,
  Yesterday, nD ago).
- Expiring signed image URLs are re-cached automatically on later refreshes.

### Changed
- Retry backoff after a refresh timeout is gentler and shows a countdown.

### Fixed
- Translation rate limiting, with a clear error state when a provider fails.
- `srcset` values with a leading comma, which broke inline images on some sites.
- Pages whose HTTP header omits the charset now honour the HTML meta charset.
- Feed favicons resolve through the site's real URL.
- Articles are ordered by publish date rather than insert order.

## 2026-04

### Added
- Fever API image and favicon extensions.

### Changed
- Offline caching defaults to off and is labelled Experimental.

### Fixed
- The scheduler could stop silently, or stall on image URLs.
- Images wrapped in `<figure>` or links now display at full width without
  distortion. Inline `sizes` attributes and styles are stripped from parsed
  content.
- Pages blocked by an anti-bot challenge fail instead of being stored as the
  article.
- Feed retry backoff.

## 2026-03

The first month: the parser, then the feed system, then the reader.

### Added
- **Parser tuning tool.** Paste a URL to see Readability's extraction as RSS
  fields beside the original page, with lazy-loaded images converted to standard
  `<img>` tags.
- **CSS selector overrides** per field, with a DevTools-style element picker that
  produces site-wide selectors rather than article-specific ones.
- **Publish-date detection** from page metadata and content, including natural-
  language dates in English, Chinese and Japanese, with a freshness sanity check.
- **Image recovery** for images Readability drops, with a boilerplate filter that
  keeps navigation and ad images out. It falls back to the `<article>` element
  when Readability extracts too little.
- **Feed management dashboard** with link discovery and full-text RSS output.
- **Reader app**: an installable PWA with offline caching in IndexedDB, local
  image caching, URL routing with readable slugs, mobile swipe and back-button
  navigation, and a settings pane.

### Fixed
- Many early reader fixes: offline cold start, stale unread counts, and mobile
  back navigation after a fresh sign-in.
