// Single source of truth for IndexedDB name and version in src/.
// public/service-worker.js is a classic worker and cannot import from src/,
// so it duplicates these values — keep both in sync when bumping.
export const DB_NAME = 'reader-db';
export const DB_VERSION = 5;
