import { useEffect, useRef } from 'react';
import { preCacheContentAndImages } from '../sync';
import { getArticle } from '../api';
import { getArticleFromDB, saveArticlesToDB } from '../db';

/**
 * Pre-fetches adjacent articles when an article is opened.
 *
 * Tier 1 (N±1): Full cache — HTML + images via preCacheContentAndImages.
 * Tier 2 (N±2, N±3): HTML only — fetches article body and saves to IDB.
 *
 * Both tiers run in parallel and skip articles already cached.
 * Controlled by the 'adjacentPrefetch' localStorage setting (default: enabled).
 */
export function useAdjacentPrefetch({ filteredArticles, selectedArticleId, isOffline, enabled = true }) {
  const filteredArticlesRef = useRef(filteredArticles);
  filteredArticlesRef.current = filteredArticles;

  useEffect(() => {
    if (!enabled) return;
    if (!selectedArticleId) return;
    if (isOffline) return;
    if (localStorage.getItem('adjacentPrefetch') === 'false') return;

    let cancelled = false;

    const articles = filteredArticlesRef.current;
    const idx = articles.findIndex(a => String(a.id) === String(selectedArticleId));
    if (idx < 0) return;

    // Tier 1: ±1, full cache (HTML + images)
    const tier1 = [articles[idx - 1], articles[idx + 1]]
      .filter(Boolean)
      .filter(a => !a.offline_cache_complete);

    // Tier 2: ±2 and ±3, HTML only
    const tier2Candidates = [
      articles[idx - 2],
      articles[idx - 3],
      articles[idx + 2],
      articles[idx + 3],
    ].filter(Boolean);

    const tier1Task = tier1.length > 0
      ? preCacheContentAndImages(tier1).catch(err => {
          if (!cancelled) console.warn('[prefetch] Tier 1 failed', err);
        })
      : Promise.resolve();

    const tier2Tasks = tier2Candidates.map(async (article) => {
      try {
        if (cancelled) return;
        const existing = await getArticleFromDB(article.id);
        if (cancelled || existing?.content) return;
        const full = await getArticle(article.id);
        if (cancelled) return;
        await saveArticlesToDB([full]);
      } catch (err) {
        if (!cancelled) console.warn(`[prefetch] Tier 2 failed for article ${article.id}`, err);
      }
    });

    Promise.all([tier1Task, ...tier2Tasks]);

    return () => { cancelled = true; };
  }, [enabled, selectedArticleId, isOffline]);
}
