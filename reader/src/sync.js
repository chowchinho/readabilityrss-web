import { getFeeds, getArticles, getArticle, getArticleImages, API_URL } from './api';
import {
  getSyncTimestamp,
  setSyncTimestamp,
  setCheckTimestamp,
  saveArticlesToDB,
  saveFeedsDataToDB,
  getArticlesFromDB,
  getArticleFromDB,
  deleteArticleFromDB,
  getSettings,
  setArticleOfflineCacheState,
  getFeedsDataFromDB,
  saveArticleImagesToDB
} from './db';
import { IMAGES_CACHE as IMAGE_CACHE_NAME, FAVICONS_CACHE as FAVICON_CACHE_NAME } from './constants/caches';

const PRECACHE_DELAY_MS = 300;
const CONCURRENT_ARTICLES = 3;

const UNLIMITED_STORAGE_MB = 999999;
const RETENTION_BACKFILL_PAGE_SIZE = 1000;
const RETENTION_BACKFILL_MAX_PAGES = 20;

async function preCacheImageUrls(imageUrls) {
  const uniqueUrls = Array.from(new Set(imageUrls.filter(Boolean)));
  if (uniqueUrls.length === 0) return true;

  const cache = await caches.open(IMAGE_CACHE_NAME);
  let allCached = true;

  for (const url of uniqueUrls) {
    try {
      const matched = await cache.match(url);
      if (matched) continue;

      const resp = await fetch(url, { credentials: 'same-origin' });
      if (resp.ok) {
        await cache.put(url, resp.clone());
      } else {
        allCached = false;
      }
    } catch (err) {
      console.warn(`Failed to cache image ${url}`, err);
      allCached = false;
    }
  }

  return allCached;
}

function getArticleTimestamp(article) {
  return new Date(article.pub_date || article.created_at || 0).getTime();
}

function getCachedImageUrlsForArticle(article) {
  const urls = new Set();

  if (article?.main_image_proxy) {
    urls.add(
      article.main_image_proxy.startsWith('http')
        ? article.main_image_proxy
        : `${API_URL}${article.main_image_proxy}`
    );
  }

  if (!article?.content) {
    return urls;
  }

  try {
    const doc = new DOMParser().parseFromString(article.content, 'text/html');
    doc.querySelectorAll('img[src]').forEach((img) => {
      const src = img.getAttribute('src');
      if (!src) return;
      if (src.startsWith('http')) {
        urls.add(src);
      } else if (src.startsWith('/')) {
        urls.add(`${API_URL}${src}`);
      }
    });
  } catch (err) {
    console.warn(`Failed to parse cached images for article ${article.id}`, err);
  }

  return urls;
}

async function pruneImageCache(retainedArticles) {
  const retainedImageUrls = new Set();
  retainedArticles.forEach((article) => {
    getCachedImageUrlsForArticle(article).forEach((url) => retainedImageUrls.add(url));
  });

  const cache = await caches.open(IMAGE_CACHE_NAME);
  const keys = await cache.keys();
  await Promise.all(
    keys.map(async (request) => {
      if (!retainedImageUrls.has(request.url)) {
        await cache.delete(request);
      }
    })
  );
}

async function pruneFaviconCache(feedsData) {
  const retainedFaviconUrls = new Set(
    (feedsData.categories || []).flatMap((cat) =>
      (cat.feeds || []).map((feed) => `${API_URL}${feed.favicon_url}`)
    )
  );

  const cache = await caches.open(FAVICON_CACHE_NAME);
  const keys = await cache.keys();
  await Promise.all(
    keys.map(async (request) => {
      if (!retainedFaviconUrls.has(request.url)) {
        await cache.delete(request);
      }
    })
  );
}

async function enforceOfflineStorageBudget(retainedArticles, maxStorageMB, protectedArticleIds = null) {
  if (
    !navigator.storage?.estimate ||
    !Number.isFinite(maxStorageMB) ||
    maxStorageMB <= 0 ||
    maxStorageMB >= UNLIMITED_STORAGE_MB
  ) {
    return retainedArticles;
  }

  const maxBytes = maxStorageMB * 1024 * 1024;
  let { usage = 0 } = await navigator.storage.estimate();
  if (usage <= maxBytes) {
    return retainedArticles;
  }

  const removable = [...retainedArticles].sort((a, b) => {
    const aProtected = protectedArticleIds?.has(a.id) ? 1 : 0;
    const bProtected = protectedArticleIds?.has(b.id) ? 1 : 0;
    if (aProtected !== bProtected) {
      return aProtected - bProtected;
    }
    if (!!a.is_read !== !!b.is_read) {
      return a.is_read ? -1 : 1;
    }
    return getArticleTimestamp(a) - getArticleTimestamp(b);
  });

  const retainedMap = new Map(retainedArticles.map((article) => [article.id, article]));

  for (const article of removable) {
    if (protectedArticleIds?.has(article.id) && retainedMap.size > protectedArticleIds.size) {
      continue;
    }
    await deleteArticleFromDB(article.id);
    retainedMap.delete(article.id);
    await pruneImageCache(Array.from(retainedMap.values()));

    ({ usage = 0 } = await navigator.storage.estimate());
    if (usage <= maxBytes) {
      break;
    }
  }

  return Array.from(retainedMap.values());
}

/**
 * Prefetches full content and all images for a list of articles.
 * Uses concurrency control to avoid overloading the network/server.
 */
export async function preCacheContentAndImages(articles, onProgress) {
  const total = articles.length;
  if (total === 0) {
    if (onProgress) onProgress(100);
    return;
  }

  // Process articles in small chunks
  for (let i = 0; i < articles.length; i += CONCURRENT_ARTICLES) {
    const chunk = articles.slice(i, i + CONCURRENT_ARTICLES);
    await Promise.all(chunk.map(async (article) => {
      try {
        await setArticleOfflineCacheState(article.id, false);

        // 1. Ensure full content is cached in IndexedDB
        let full = await getArticleFromDB(article.id);
        if (!full || !full.content) {
          full = await getArticle(article.id);
          await saveArticlesToDB([full]);
        }

        // 2. Fetch all image URLs for this article
        const { images } = await getArticleImages(article.id);
        if (images && images.length > 0) {
          await saveArticleImagesToDB(article.id, images);
        }

        // 3. Cache the full image set used by the article, including the main image if present.
        const proxyUrls = images.map(img => (
          img.proxy.startsWith('http') ? img.proxy : `${API_URL}${img.proxy}`
        ));

        if (full?.main_image_proxy) {
          proxyUrls.unshift(
            full.main_image_proxy.startsWith('http')
              ? full.main_image_proxy
              : `${API_URL}${full.main_image_proxy}`
          );
        }

        const imagesCached = await preCacheImageUrls(proxyUrls);
        await setArticleOfflineCacheState(article.id, imagesCached);
      } catch (err) {
        console.warn(`Failed to pre-cache article ${article.id}`, err);
      }
    }));
    
    if (onProgress) {
      const progress = Math.min(100, Math.round(((i + chunk.length) / total) * 100));
      onProgress(progress);
    }

    // Gap between article chunks
    await new Promise(r => setTimeout(r, PRECACHE_DELAY_MS));
  }

  if (onProgress) onProgress(0); // Reset when done
}

async function preCacheFavicons(feedsData) {
  const cache = await caches.open(FAVICON_CACHE_NAME);
  for (const cat of feedsData.categories) {
    for (const feed of cat.feeds) {
      try {
        const url = `${API_URL}${feed.favicon_url}`;
        const match = await cache.match(url);
        if (!match) {
          const resp = await fetch(url);
          if (resp.ok) await cache.put(url, resp);
        }
      } catch (err) {
        console.warn('Failed to cache favicon', err);
      }
    }
  }
}

async function fetchRetentionWindowArticles(sinceIso) {
  const all = [];
  let offset = 0;

  for (let page = 0; page < RETENTION_BACKFILL_MAX_PAGES; page += 1) {
    // Background caching pages by offset, so it always uses chronological order —
  // a shuffled or rescored page would make offsets meaningless between requests.
  const resp = await getArticles(sinceIso, RETENTION_BACKFILL_PAGE_SIZE, offset, null, 'latest');
    const pageArticles = resp?.articles || [];
    if (pageArticles.length === 0) break;

    all.push(...pageArticles);
    if (pageArticles.length < RETENTION_BACKFILL_PAGE_SIZE) break;
    offset += RETENTION_BACKFILL_PAGE_SIZE;
  }

  return all;
}

export async function applyLocalCachePolicy(settingsOverride = null, feedsDataOverride = null, protectedArticleIds = null) {
  const settings = settingsOverride || await getSettings();
  const retentionMs = settings.retentionDays * 24 * 60 * 60 * 1000;
  const cutoffTime = Date.now() - retentionMs;

  let allLocalArticles = await getArticlesFromDB();
  for (const article of allLocalArticles) {
    const articleTime = getArticleTimestamp(article);
    if (articleTime < cutoffTime) {
      await deleteArticleFromDB(article.id);
    }
  }

  let retainedArticles = (await getArticlesFromDB()).filter(
    (article) => getArticleTimestamp(article) >= cutoffTime
  );
  retainedArticles = await enforceOfflineStorageBudget(retainedArticles, settings.maxStorageMB, protectedArticleIds);
  await pruneImageCache(retainedArticles);

  const isCachingEnabled = localStorage.getItem('offlineCaching') === 'true';
  if (!isCachingEnabled) {
    await caches.delete(FAVICON_CACHE_NAME);
    return;
  }

  const feedsData = feedsDataOverride || await getFeedsDataFromDB();
  await pruneFaviconCache(feedsData);
}

export async function runSync(onProgress, onDataReady) {
  await setCheckTimestamp(new Date().toISOString());
  
  const lastSync = await getSyncTimestamp();
  const settings = await getSettings();

  // Fetch feeds with retention window cutoff
  const since = new Date(Date.now() - (settings.retentionDays * 24 * 60 * 60 * 1000)).toISOString();
  const feedsData = await getFeeds(since);
  await saveFeedsDataToDB(feedsData);

  // Fetch delta articles since last sync timestamp.
  const limit = 500;
  const articlesResp = await getArticles(lastSync, limit, 0, null, 'latest');

  if (articlesResp.articles.length > 0) {
    await saveArticlesToDB(articlesResp.articles);
  }

  if (onDataReady) {
    await onDataReady({
      newArticlesCount: articlesResp.articles.length,
      feedsData,
      articles: articlesResp.articles,
    });
  }

  // Backfill all articles inside the configured retention window.
  // This keeps local cache counts aligned when retention increases (e.g. 3 -> 7 days),
  // instead of relying only on delta updates.
  try {
    const windowArticles = await fetchRetentionWindowArticles(since);
    if (windowArticles.length > 0) {
      await saveArticlesToDB(windowArticles);
    }
  } catch (err) {
    console.warn('Retention window backfill failed (non-critical):', err);
  }

  await applyLocalCachePolicy(settings, feedsData);
  const retentionMs = settings.retentionDays * 24 * 60 * 60 * 1000;
  const cutoffTime = Date.now() - retentionMs;
  const allLocalArticles = (await getArticlesFromDB()).filter(
    (article) => getArticleTimestamp(article) >= cutoffTime
  );

  await setSyncTimestamp(articlesResp.sync_timestamp);

  // Pre-cache content, images and favicons in the background if enabled.
  // Offline reading expects full article bodies to exist locally, not just list metadata.
  const isCachingEnabled = localStorage.getItem('offlineCaching') === 'true';
  
  if (isCachingEnabled) {
    const faviconTask = preCacheFavicons(feedsData).catch(err => console.warn('Favicon caching failed', err));
    let cacheTask = Promise.resolve();
    
    const articlesNeedingOfflineContent = allLocalArticles
      .filter(a => !a.offline_cache_complete)
      .sort((a, b) => {
        if (!!a.is_read === !!b.is_read) return 0;
        return a.is_read ? 1 : -1;
      });

    const justCachedIds = new Set(articlesNeedingOfflineContent.map(a => a.id));

    if (articlesNeedingOfflineContent.length > 0) {
      cacheTask = preCacheContentAndImages(articlesNeedingOfflineContent, onProgress).catch(err => {
        console.warn('Full pre-caching failed', err);
        if (onProgress) onProgress(0);
      });
    }

    await Promise.all([faviconTask, cacheTask]);

    // Protect just-cached articles in the post-cache pass so sync does not fight itself
    await applyLocalCachePolicy(settings, feedsData, justCachedIds);
  }

  return { newArticlesCount: articlesResp.articles.length };
}
