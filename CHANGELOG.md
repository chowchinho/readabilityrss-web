# Changelog

Notable changes to ReadabilityRSS, newest first. Releases are named by date. The
dashboard shows the build time in its top bar.

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
