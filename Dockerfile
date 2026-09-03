# syntax=docker/dockerfile:1

# --- Stage 1: build both frontends -------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /build

# Manifests first so dependency layers cache independently of source edits.
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY reader/package.json reader/package-lock.json ./reader/
RUN cd frontend && npm ci
RUN cd reader && npm ci

COPY frontend ./frontend
COPY reader ./reader

# These values must match start-dev.sh. The dashboard is served under /manage and
# both apps talk to the API over relative paths, which is what makes the
# single-origin model work.
RUN cd frontend && PUBLIC_URL=/manage REACT_APP_API_URL=/api npm run build
RUN cd reader && VITE_API_URL= npm run build

# --- Stage 2: shared Python base ----------------------------------------------
FROM python:3.12-slim AS python-base

# curl is used by HEALTHCHECK. libglib2.0-0 is required by opencv-python-headless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# --- Stage 3: test ------------------------------------------------------------
# Not part of the runtime image. Build with --target test to run the suite
# against the same dependency set the runtime uses.
FROM python-base AS test
COPY backend/requirements-dev.txt ./backend/requirements-dev.txt
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
COPY backend ./backend
WORKDIR /app/backend
CMD ["python", "-m", "pytest", "tests/", "-q"]

# --- Stage 4: runtime ---------------------------------------------------------
FROM python-base AS runtime

# Only the application package. Deliberately excludes backend/tests, backend/scripts,
# backend/labels.json (read only by those scripts), backend/test_real_sites.py (which
# fetches live websites), and pytest itself. A production image should not carry its
# own test suite.
COPY backend/app ./backend/app
COPY --from=frontend-build /build/frontend/build ./frontend/build
COPY --from=frontend-build /build/reader/build ./reader/build

# The volume mount point. Owned by the app user so a fresh volume is writable.
ENV DATA_DIR=/app/backend/data
ENV PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p "$DATA_DIR" \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

# Run from /app so the backend.app.main import path resolves, matching the
# documented startup contract. Running from backend/ fails with ModuleNotFoundError.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
