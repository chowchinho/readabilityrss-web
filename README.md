# ReadabilityRSS

A self-hosted RSS reader built around one idea: **the full article, rendered well, in your
own reader** — not a truncated summary that bounces you out to a page full of consent
banners.

It fetches each article, extracts the real content, repairs the images publishers hide
behind lazy-loading, and serves the result as a clean reading experience, a set of
full-text RSS feeds, or both.

<!-- screenshot: the reader index. Add as docs/screenshots/reader-index.png -->

## Why it exists

Most feeds ship a headline and two sentences. Most extraction tools drop the images. This
project treats extraction quality as the product: when a site fights back, you can teach it
per-site CSS selectors rather than giving up on the feed.

---

## What it does

### Content extraction

- Full-article extraction with readability-lxml and BeautifulSoup
- **Image recovery** — restores images that extraction strips out, filtering navigation
  and advertising images back out again
- **Lazy-image conversion** — rewrites `data-src` and friends into real `<img src>` so
  images survive in any reader
- **Per-site CSS overrides** — when the generic extractor is wrong, point it at the right
  selectors for that site
- Publication-date detection with a freshness window
- Content tidying that strips scripts, forms, and canvases while preserving video embeds
- Optional FlareSolverr integration for sites behind Cloudflare interstitials

### Reading

- Three-column reading view with a sources index
- Article cards with autoplaying image slideshows, cycling only while on screen
- **Focal-point cropping** — on-device face and salience detection picks the crop, so
  thumbnails do not decapitate people
- Offline reading with a configurable retention window
- Installable as a PWA, plus a Capacitor Android wrapper
- Light and dark themes

### Ranking

- Vote-driven ranking that learns from what you actually open
- Exposure decay, so articles you have already been shown make room for ones you have not
- Exploration slots that deliberately surface unranked material
- A score breakdown panel showing exactly why an article placed where it did
- Automatic topic tagging and classification

### Feeds and sources

- Feed source management with categories and custom ordering
- Link discovery — point it at a page and it finds the feeds
- Scheduled background polling
- **Full-text RSS output**, so anything you subscribe to here works in any other reader
- OPML import and export
- A Fever-compatible API endpoint for Fever clients

### Translation

- DeepSeek as the primary translator with automatic Google Translate fallback
- Per-source target languages and a translation attribution badge
- Cost tracking per provider

<!-- screenshot: a single extracted article. Add as docs/screenshots/article-view.png -->

---

## Quickstart

Requires Docker and Docker Compose.

```bash
git clone https://github.com/chowchinho/readabilityrss-web.git
```

```bash
cd readabilityrss-web && cp .env.example .env && docker compose up -d
```

Then open <http://127.0.0.1:8001> and **set a username and password immediately.**

> ### Read this before exposing the port
>
> A fresh instance has **no password set, and every API route is open until you create
> one.** This is deliberate — you need an unauthenticated path to reach the setup screen —
> but it means an instance reachable from the internet before you finish setup is an
> instance anyone can use.
>
> The bundled compose file binds to `127.0.0.1` for exactly this reason. Set your password
> first, and put it behind a reverse proxy with TLS before exposing it to the internet.

### Reaching it from other machines

By default the app is published on loopback only, so it is reachable just from the machine
running Docker. To use it from a phone or laptop on the same network, set these in `.env`:

```bash
BIND_ADDR=0.0.0.0
HOST_PORT=8010
```

Then it answers on `http://<host-lan-ip>:8010`. `HOST_PORT` is also how you avoid a clash
when something else already owns 8001 on that machine — the container always listens on
8001 internally, so only the host side changes.

Everything runs as one process on port 8001:

| Path | Serves |
|---|---|
| `/` | Reader |
| `/manage/` | Management dashboard |
| `/api/…` | API |
| `/feed/…` | RSS and OPML output |
| `/fever?api` | Fever-compatible endpoint |

<!-- screenshot: the management dashboard. Add as docs/screenshots/dashboard.png -->

---

## Configuration

Everything is environment variables; see [`.env.example`](.env.example) for the annotated
list. The ones that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `backend/data` | Where `feeds.db` and the image caches live |
| `CORS_ORIGINS` | local dev only | Comma-separated allow-list; add your public hostname |
| `PUBLIC_URL` | request host | Base URL for generated RSS and OPML links |
| `DEEPSEEK_API_KEY` | unset | Primary translator; Google fallback needs no key |
| `FLARESOLVERR_URL` | unset | Cloudflare bypass; degrades gracefully when absent |
| `ALLOW_PRIVATE_IP_FETCH` | `0` | SSRF guard. Leave off unless you fetch from your own LAN |

Your data lives in the `readabilityrss-data` volume. Back that up and you have backed up
everything.

---

## Tuning extraction

When a site extracts badly, the dashboard's element picker lets you set per-source
selectors — item, title, content, date, image, and exclusions — and preview the result
against a live URL before saving.

<!-- screenshot: the element picker mid-tuning. Add as docs/screenshots/parser-tuning.png -->

---

## Development

Run the tests:

```bash
cd backend && python -m pytest tests/ -q
```

Some tests are skipped unless optional fixtures are present. Both sets are large binary
assets kept out of git; fetch them with `backend/scripts/fetch_parity_corpus.py` and
`backend/scripts/fetch_focal_fixtures.py` if you are working on extraction or cropping.

Running without Docker: the backend must start **from the project root**, not from
`backend/`, or the `backend.app.main` import fails.

```bash
uvicorn backend.app.main:app --port 8001
```

The dashboard dev server runs on 3010 and the reader's on 5173; both proxy `/api` to 8001.

> **Building the dashboard on Windows under Git Bash** needs `MSYS_NO_PATHCONV=1`.
> Without it, MSYS rewrites `PUBLIC_URL=/manage` into a Windows path and the built
> `index.html` points its assets at `C:/Program Files/Git/manage/...`, which fails
> silently at load. This does not affect Linux, macOS, or the Docker build.

### Running the test suite in Docker

The runtime image deliberately contains no tests and no pytest. To run the suite against
the same dependency set the runtime uses, build the test stage:

```bash
docker build --target test -t readabilityrss-web:test .
```

```bash
docker run --rm readabilityrss-web:test
```

## Architecture

One FastAPI process serves everything from a single origin — the reader at `/`, the
dashboard at `/manage/`, and the API. `backend/app/spa.py` provides the history fallback
plus a reserved-prefix guard, so unmatched `/api/*`, `/feed/*`, and `/fever*` return JSON
404s rather than the reader's `index.html`. Fever clients break if fed HTML, so that guard
matters.

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLite via aiosqlite, APScheduler |
| Extraction | readability-lxml, BeautifulSoup4, Pillow, OpenCV |
| Dashboard | React 18 (Create React App) |
| Reader | React 18 (Vite), service worker, IndexedDB |
| Mobile | Capacitor (Android) |

## License

MIT — see [LICENSE](LICENSE).
