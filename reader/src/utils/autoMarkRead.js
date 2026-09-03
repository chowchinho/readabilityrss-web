// reader/src/utils/autoMarkRead.js

/**
 * Decide whether opening an article should auto-mark it read.
 *
 * The effect that calls this depends on `articles`, so it re-runs on every read-state
 * mutation — including the user manually marking the open article unread. Without
 * remembering which opening was already handled it would immediately re-mark it read,
 * making the unread button look broken. `lastAutoMarkedId` is that memory: one
 * auto-mark per opening, released when the article closes.
 *
 * Returns the decision plus the next value for the caller's ref.
 */
export function resolveAutoMarkRead(selectedArticleId, article, lastAutoMarkedId) {
  if (!selectedArticleId) return { mark: false, nextAutoMarkedId: null };

  // Not in the list yet — leave the claim untouched so a later render retries.
  if (!article) return { mark: false, nextAutoMarkedId: lastAutoMarkedId };

  if (String(lastAutoMarkedId) === String(selectedArticleId)) {
    return { mark: false, nextAutoMarkedId: lastAutoMarkedId };
  }

  return { mark: !article.is_read, nextAutoMarkedId: article.id };
}
