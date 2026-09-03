// Single source of truth for CacheStorage names in src/.
// public/service-worker.js is a classic worker and cannot import from src/,
// so it duplicates these three values — keep both in sync when bumping.
export const APP_SHELL_CACHE = 'reader-app-shell-v6';
export const IMAGES_CACHE = 'reader-images-v6';
export const FAVICONS_CACHE = 'reader-favicons-v6';
