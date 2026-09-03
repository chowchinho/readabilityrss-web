"""Static file serving with SPA history fallback."""
import os
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


class SpaStaticFiles(StaticFiles):
    """Serve a built SPA, falling back to index.html for client-side routes.

    reserve_api_paths guards the root mount: without it an unmatched /api/*
    request would fall through to index.html and return HTML with status 200,
    so a broken API call would silently receive a web page.
    """

    # Hashed bundle filenames cache-bust themselves; these do not, so a CDN
    # would keep serving the previous deploy's copy. Cloudflare caches .js by
    # default, which pinned a stale service worker for hours after each deploy
    # and silently broke its background sync against a newer IndexedDB version.
    NO_CACHE_FILES = frozenset({"service-worker.js"})

    def __init__(self, *args, reserve_api_paths: bool = False, **kwargs):
        self.reserve_api_paths = reserve_api_paths
        super().__init__(*args, **kwargs)

    async def get_response(self, path, scope):
        # StaticFiles.get_path() runs os.path.normpath, so on Windows the
        # separators arrive as backslashes.
        url_path = path.replace(os.sep, "/")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            if self.reserve_api_paths and (
                url_path == "fever" or url_path.startswith(("api/", "feed/", "fever/"))
            ):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            if not os.path.exists(os.path.join(self.directory, "index.html")):
                raise
            response = await super().get_response("index.html", scope)

        if url_path in self.NO_CACHE_FILES:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
