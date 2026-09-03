"""FastAPI application setup for ReadabilityRSS"""
import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

# Ensure scheduler/app log messages (INFO+) are visible in the backend log
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s — %(message)s")
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.gzip import (
    GZipMiddleware as StarletteGZipMiddleware,
)
from starlette.middleware.gzip import (
    GZipResponder as StarletteGZipResponder,
)
from starlette.middleware.gzip import (
    Headers,
)


class CustomGZipResponder(StarletteGZipResponder):
    async def send_with_compression(self, message) -> None:
        await super().send_with_compression(message)
        if message.get("type") == "http.response.start":
            headers = Headers(raw=message["headers"])
            ct = headers.get("content-type", "")
            if ct.startswith("image/"):
                self.content_type_is_excluded = True


class GZipMiddleware(StarletteGZipMiddleware):
    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if "gzip" in headers.get("Accept-Encoding", ""):
            responder = CustomGZipResponder(self.app, self.minimum_size, compresslevel=self.compresslevel)
        else:
            responder = StarletteGZipResponder(self.app, self.minimum_size, compresslevel=self.compresslevel)
        await responder(scope, receive, send)
from .database import db
from .routes.auth import router as auth_router
from .routes.categories import router as categories_router
from .routes.discover import router as discover_router
from .routes.events import router as events_router
from .routes.feed_sources import router as feed_sources_router
from .routes.feeds import router as feeds_router
from .routes.fever import router as fever_router
from .routes.parse import router as parse_router
from .routes.ranking import router as ranking_router
from .routes.reader import router as reader_router
from .routes.settings import router as settings_router
from .routes.translation_usage import router as translation_usage_router
from .services.scheduler import start_scheduler
from .spa import SpaStaticFiles


async def _snippet_backfill_loop():
    """Fill snippets for articles parsed before the columns existed.

    Runs after the app is serving, not inside init(), because the full pass is
    roughly 15 seconds of CPU and would block every restart for that long.
    """
    while True:
        try:
            updated = await db.backfill_snippets(200)
        except Exception:
            logger.exception("Snippet backfill stopped")
            return
        if updated == 0:
            return
        # Not sleep(0), which only drains callbacks already queued. Each batch is a few
        # hundred ms of synchronous parsing, so give requests a real window between them.
        await asyncio.sleep(0.05)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init()
    start_scheduler()
    backfill_task = asyncio.create_task(_snippet_backfill_loop())
    try:
        yield
    finally:
        # Cancel before close: closing the connection underneath a running batch
        # raises inside the task during shutdown.
        backfill_task.cancel()
        await db.close()

app = FastAPI(
    title="ReadabilityRSS API",
    description="API for extracting readable content from RSS feeds",
    version="0.1.0",
    lifespan=lifespan,
)
# Paths that do not require authentication
AUTH_EXEMPT_PATTERNS = [
    re.compile(r"^/api/auth/(status|setup|login)$"),
    re.compile(r"^/api/reader/image-proxy"),
    re.compile(r"^/api/reader/cached-image/"),
    re.compile(r"^/api/reader/favicon/"),
    re.compile(r"^/api/reader/ranking/scores"),
    re.compile(r"^/api/reader/ranking/feedback"),
    re.compile(r"^/api/reader/focal-points$"),
    re.compile(r"^/health$"),
    re.compile(r"^/fever/?$"),
    re.compile(r"^/feed/.+/rss$"),
    re.compile(r"^/feed/opml$"),
    re.compile(r"^/docs"),
    re.compile(r"^/openapi\.json$"),
]

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Skip auth for CORS preflight requests
    if request.method == "OPTIONS":
        return await call_next(request)

    # Only protect /api/* routes (except exempt ones)
    if path.startswith("/api/"):
        exempt = any(p.match(path) for p in AUTH_EXEMPT_PATTERNS)
        if not exempt:
            # Check if web_auth is set up — if not, skip auth (first-time use)
            try:
                auth = await db.get_web_auth()
            except Exception as e:
                # Fail closed. Treating a DB error as "not configured yet" skipped
                # authentication for the request across all of /api/*.
                logger.error(f"Auth check database error: {e}")
                return JSONResponse(status_code=503, content={"detail": "Authentication service unavailable"})
            if auth is not None:
                token = request.headers.get("authorization", "")
                if token.startswith("Bearer "):
                    token = token[7:]
                else:
                    token = ""
                try:
                    is_valid = await db.validate_session_token(token)
                except Exception as e:
                    logger.error(f"Session validation database error: {e}")
                    return JSONResponse(status_code=503, content={"detail": "Authentication service unavailable"})
                if not is_valid:
                    return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    return await call_next(request)

# Include routes
app.include_router(auth_router)
app.include_router(parse_router)
app.include_router(discover_router)
app.include_router(feed_sources_router)
app.include_router(categories_router)
app.include_router(feeds_router)
app.include_router(fever_router)
app.include_router(reader_router)
app.include_router(ranking_router)
app.include_router(events_router)
app.include_router(translation_usage_router)
app.include_router(settings_router)

# Add GZip middleware before CORS so CORS headers wrap gzipped responses
app.add_middleware(GZipMiddleware, minimum_size=500)

# Add CORS middleware LAST so it wraps all other middlewares and routes
# Origins allowed by default: local dev servers plus the Capacitor Android bundle,
# which is served from the app package rather than over HTTP. Deployments add their
# own public hostname via CORS_ORIGINS rather than editing this list.
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3010",
    "http://localhost:5173",
    "http://127.0.0.1:3010",
    "http://127.0.0.1:5173",
    "https://localhost",
    "http://localhost",
    "capacitor://localhost",
]


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "")
    parsed = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return parsed or DEFAULT_CORS_ORIGINS


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1):(3010|5173|8001)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

@app.get("/manage")
async def redirect_manage():
    """Redirect /manage to /manage/ so Starlette static mount serves Dashboard SPA"""
    return RedirectResponse(url="/manage/", status_code=307)

# Mount the built SPAs last so every API route above wins.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_BUILD_DIR = os.path.join(PROJECT_ROOT, "frontend", "build")
READER_BUILD_DIR = os.path.join(PROJECT_ROOT, "reader", "build")
os.makedirs(FRONTEND_BUILD_DIR, exist_ok=True)
os.makedirs(READER_BUILD_DIR, exist_ok=True)

app.mount("/manage", SpaStaticFiles(directory=FRONTEND_BUILD_DIR, html=True), name="manage")
app.mount(
    "/",
    SpaStaticFiles(directory=READER_BUILD_DIR, html=True, reserve_api_paths=True),
    name="reader",
)
