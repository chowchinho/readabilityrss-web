# Changelog

Notable changes to ReadabilityRSS, newest first. Releases are named by date. The
dashboard shows the build time in its top bar.

## 2026-10-01 — Saved articles

### Added
- **Save articles to read later.** A bookmark on the reading page saves or unsaves the
  article: in the toolbar on desktop, and on phones as a button beside the rating shortcut
  and as the first item of the More menu.
- **Saved list.** A "Saved" row under All Articles, in the sidebar and on the phone Feeds
  screen, lists saved articles with the most recently saved first, read or unread and
  however old. It appears only once something is saved; a new setting, "Show Saved only
  when something is saved", can keep it visible with an empty state instead.
- API: `POST /api/reader/articles/{id}/save` and `/unsave`, a `saved=true` option on
  `GET /api/reader/articles`, `saved_at` on articles, and `total_saved` on
  `GET /api/reader/feeds`. Saving through the Fever API also records `saved_at`.

### Fixed
- Saved articles are no longer deleted when their feed grows past the per-feed article
  limit, and the offline cache keeps them past the retention window.

## 2026-10-01 — Phone reading page and navigation

The rest of the phone redesign that followed 3.0.0. Desktop is unchanged.

### Added
- **Feeds screen on phones.** The sidebar is now a page of its own rather than an
  overlay: a large title, a feed search, grouped lists with the unread total for each
  category, settings in the top corner, and sync status and theme at the bottom. A feed
  slides its article list in from the right, "‹ Feeds" goes back, and the system back
  button steps from article to list to Feeds before asking to leave.
- **Pull to refresh** on the phone article list and index, with rubber-band resistance,
  a single vibration at the threshold, and a quick flick that also commits. Live mode,
  which had no refresh control before, reloads feeds and articles.
- **Swipe rows.** Swipe left on an article to reveal Read and Hide; swipe right past
  the threshold to flip its read state.
- **Reading toolbar at the bottom.** The reading page's actions sit in a floating
  capsule within thumb reach: read state, translate, text size, share and More (open
  original, copy link, why this is here, hide article). It hides while scrolling down
  and returns on scroll up and at the end of the article.
- **One translate button, three states:** it starts an on-demand translation, shows a
  progress ring while the translation streams, and once translated opens the
  Original / Side by side / Translation switch.
- **End-of-article card** asking "Worth reading?", followed by a card for the next
  article.

### Changed
- The phone article list has a light app bar: a large title with the unread count that
  hands over to a compact title as you scroll, and a bottom bar with "Unread only" and
  "Mark all read" (which asks once more before acting).
- A main image at least as wide as the column becomes a full-bleed hero on the phone
  reading page, and the same image is not repeated in the body.
- Phone card text: headlines take two lines and summaries three, or four when the
  headline fits on one line.
- The on-demand translation stream now reports how many blocks it will translate, so
  progress is exact rather than estimated. Pending paragraphs pulse until translated.

### Fixed
- The reading toolbar no longer sits under Android's navigation bar.
- A Google Translate throttle test no longer fails on a machine started less than half
  an hour earlier.

## 2026-10-01 — 3.0.0: UI/UX redesign

A full design pass on the reader, built on Base UI and Apple's interface guidelines.
This is the first release with a version number; earlier entries stay named by date.

### Added
- **Translation view switch.** A translated article shows "Translated from {language} ·
  {provider}" under the title, with an Original / Side by side / Translation switch.
  All three views are built in the browser from the stored text, so no new API call is
  needed. The choice is saved per device, carries across articles, and switching keeps
  the same passage at the same height on screen.
- **Text size control** in the reading toolbar: five steps on top of the base size,
  saved per device, phones included.
- **Top picks on phones.** In smart sort, the top 10% of loaded articles by score get a
  full-width 16:9 card with a "Top pick" label, at least four rows apart. A card keeps
  its size for the session, so the list never reshuffles under your thumb, and an image
  under 600px wide falls back to the square layout instead of being upscaled.
- `GET /api/reader/articles` and `GET /api/reader/articles/{id}` return
  `translated_from` and `translated_to`.

### Changed
- **Base UI** powers the Settings drawer, the category order dialog, the ranking info
  popover, toasts and the exit prompt on Android, with focus trapping and enter/exit
  animations that leave the way they came.
- **Phone article list:** square thumbnails at 30% of the screen width (up from 88px)
  sit beside the source line, headlines get three lines for CJK titles, and the unread
  dot moves to the leading edge.
- **Reading pane:** an icon-only toolbar that the article scrolls beneath, a reading
  progress hairline, wide photos that break out of the text column on larger panes,
  and 44px touch targets. A swiped-to article enters from the side the last one left.
- **Index grid:** hovering a card raises the headline into the foot of the photo and
  shows the start of the article beneath it. Single-feed cards carry the time on the
  photo, and the "why this is here" button sits with the thumbs.
- Spacing, type scale and press feedback were reworked across the sidebar, grid and
  reader. Hover effects are limited to devices with a fine pointer, and reduced
  motion, reduced transparency and increased contrast are respected throughout.

### Fixed
- Sharing without the system share sheet shows a toast instead of a browser alert.
- The card slideshow no longer logs a React style warning.

## 2026-09-26

### Fixed
- **Links in local-model translations no longer show up as raw markup.** The local
  model sometimes hands a link back spaced out and with typographic quotes, such as
  `< a href = “ /article/ ” >`, which then appeared as visible text. Such a tag is now
  rebuilt into a real link when it has a matching closer, with the address taken from
  the original rather than the model's copy, and dropped when it has none.

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
