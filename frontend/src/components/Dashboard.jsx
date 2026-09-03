import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  toAbsoluteBackendUrl,
  getFeedSources,
  deleteFeedSource,
  checkFeedSource,
  generateFeed,
  refreshAllFeeds,
  getFeedArticles,
  getRefreshStatus,
  toggleFeedSourceEnabled,
  flushFeedArticles,
  getTranslationUsage
} from '../api';
import AddWebsite from './AddWebsite';
import googleTranslateIcon from '../assets/google-translate.svg';
import deepseekIcon from '../assets/deepseek.svg';
import './Dashboard.css';

function absolutizeArticleHtml(html) {
  if (!html) return html;
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll('img[src], source[srcset]').forEach((node) => {
    if (node.hasAttribute('src')) {
      node.setAttribute('src', toAbsoluteBackendUrl(node.getAttribute('src')));
    }
    if (node.hasAttribute('srcset')) {
      const srcset = node.getAttribute('srcset') || '';
      const rewritten = srcset
        .split(',')
        .map((entry) => {
          const parts = entry.trim().split(/\s+/, 2);
          if (!parts[0]) return entry;
          const absolute = toAbsoluteBackendUrl(parts[0]);
          return [absolute, parts[1]].filter(Boolean).join(' ');
        })
        .join(', ');
      node.setAttribute('srcset', rewritten);
    }
  });
  return doc.body.innerHTML;
}

function elapsed(ts) {
  if (!ts) return '—';
  const diff = Date.now() - new Date(ts.replace(' ', 'T') + 'Z').getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return mins + 'm';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h';
  return Math.floor(hrs / 24) + 'd';
}

function formatPubDateRelative(dateStr) {
  if (!dateStr) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(dateStr);
  if (!m) return { label: dateStr, tier: 'mid' };
  const articleDate = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((today.getTime() - articleDate.getTime()) / 86400000);
  // diffDays === -1 means the article's pub_date is "tomorrow" locally — this
  // happens when a source in an Asian timezone publishes during its today
  // while we're still on the previous calendar day. Treat as Today.
  if (diffDays < -1) return { label: dateStr, tier: 'mid' };
  let label;
  if (diffDays <= 0) label = 'Today';
  else if (diffDays === 1) label = 'Yesterday';
  else label = `${diffDays}D ago`;
  const tier = diffDays <= 1 ? 'fresh' : diffDays > 7 ? 'old' : 'mid';
  return { label, tier };
}

function formatCountdown(ms) {
  const totalMin = Math.max(0, Math.floor(ms / 60000));
  if (totalMin < 60) return `${totalMin}m`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

function TimeoutStatus({ source }) {
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);
  const retryAt = source.next_check_at
    ? new Date(source.next_check_at.replace(' ', 'T') + 'Z').getTime()
    : null;
  const remaining = retryAt != null ? retryAt - now : null;
  const count = source.error_count || 0;
  let tail;
  if (remaining == null) {
    tail = '';
  } else if (remaining <= 0) {
    tail = ' · retry due — waiting for scheduler';
  } else {
    tail = ` · next try in ${formatCountdown(remaining)}`;
  }
  return (
    <span className="timeout-status">
      {source.last_error}{count > 0 ? ` · retry ${count}` : ''}{tail}
    </span>
  );
}

function startColResize(e) {
  e.preventDefault();
  const handle = e.currentTarget;
  handle.classList.add('dragging');
  const startX = e.clientX;
  const startWidth = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue('--col-url-width'), 10
  );
  const onMove = (e) => {
    const w = Math.max(80, Math.min(400, startWidth + e.clientX - startX));
    document.documentElement.style.setProperty('--col-url-width', w + 'px');
  };
  const onUp = () => {
    handle.classList.remove('dragging');
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

function CollapsibleCategory({ category, sources, onCheck, onDelete, onEdit, onFlush, onGenerate, onPreview, onTitleClick, onToggleEnabled, refreshingSourceId, parseProgress, queuedSourceIds }) {
  const [collapsed, setCollapsed] = useState(false);
  const [animating, setAnimating] = useState(false);
  const contentRef = useRef(null);

  const handleToggle = () => {
    if (!contentRef.current) { setCollapsed(!collapsed); return; }
    if (collapsed) {
      const height = contentRef.current.scrollHeight + 'px';
      contentRef.current.style.maxHeight = '0';
      contentRef.current.offsetHeight; // eslint-disable-line no-unused-expressions
      contentRef.current.style.maxHeight = height;
      setAnimating(true);
      setCollapsed(false);
      setTimeout(() => {
        if (contentRef.current) contentRef.current.style.maxHeight = 'none';
        setAnimating(false);
      }, 300);
    } else {
      const height = contentRef.current.scrollHeight + 'px';
      contentRef.current.style.maxHeight = height;
      contentRef.current.offsetHeight; // eslint-disable-line no-unused-expressions
      contentRef.current.style.maxHeight = '0';
      setAnimating(true);
      setCollapsed(true);
      setTimeout(() => setAnimating(false), 300);
    }
  };

  return (
    <div className={`category-group ${collapsed ? 'collapsed' : ''}`}>
      <div className="category-header" onClick={handleToggle}>
        <span className="category-chevron">{collapsed ? '▶' : '▼'}</span>
        <span className="category-name">{category}</span>
        <span className="category-count">({sources.length})</span>
      </div>
      <div
        className={`sources-list ${animating ? 'animating' : ''}`}
        ref={contentRef}
        style={{ maxHeight: collapsed && !animating ? '0' : (!collapsed && !animating ? 'none' : undefined) }}
      >
        {sources.map(source => (
          <SourceRow
            key={source.id}
            source={source}
            onCheck={onCheck}
            onDelete={onDelete}
            onEdit={onEdit}
            onFlush={onFlush}
            onGenerate={onGenerate}
            onPreview={onPreview}
            onTitleClick={onTitleClick}
            onToggleEnabled={onToggleEnabled}
            isRefreshing={source.id === refreshingSourceId}
            parseProgress={source.id === refreshingSourceId ? parseProgress : null}
            isQueued={queuedSourceIds && queuedSourceIds.includes(source.id)}
          />
        ))}
      </div>
    </div>
  );
}

function SourceRow({ source, onCheck, onDelete, onEdit, onFlush, onGenerate, onPreview, onTitleClick, onToggleEnabled, isRefreshing, parseProgress, isQueued }) {
  const [feedback, setFeedback] = React.useState(null);
  const [feedbackKey, setFeedbackKey] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const feedbackTimer = React.useRef(null);
  const isDisabled = !source.enabled;

  useEffect(() => {
    return () => {
      if (feedbackTimer.current) {
        clearTimeout(feedbackTimer.current);
      }
    };
  }, []);

  const showFeedback = (msg) => {
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    setFeedback(msg);
    setFeedbackKey(k => k + 1);
    feedbackTimer.current = setTimeout(() => setFeedback(null), 2400);
  };

  const handleAction = (action, label) => async () => {
    if (busy) return;
    setBusy(true);
    showFeedback(label + '…');
    try {
      await action(source.id);
      showFeedback('Done');
    } catch {
      showFeedback('Failed');
    } finally {
      setBusy(false);
    }
  };

  const handleCopyRSS = (e) => {
    const feedUrl = `${window.location.origin}/feed/${source.id}/rss`;
    const btn = e.currentTarget;
    const doCopy = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(feedUrl)
      : new Promise((resolve) => {
          const ta = document.createElement('textarea');
          ta.value = feedUrl;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          resolve();
        });
    doCopy
      .then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy RSS'; }, 1500);
      })
      .catch((err) => {
        console.warn('Failed to copy RSS URL:', err);
      });
  };

  const ts = source.last_generation_at || source.last_fetch_at;
  const latestTitle = source.latest_title;
  const hasError = source.status === 'error' || source.status === 'red';

  return (
    <div className={`source-row ${isDisabled ? 'source-disabled' : ''}`}>
      <div
        className={`status-indicator ${isDisabled ? 'status-disabled' : `status-${source.status}`}`}
        title={isDisabled ? 'Disabled — click to enable' : (source.last_error || 'OK — click to disable')}
        onClick={() => onToggleEnabled(source.id)}
        style={{ cursor: 'pointer' }}
      />
      <div className="source-url-cell">
        <a href={source.url} target="_blank" rel="noopener noreferrer">
          {source.name}
        </a>
        {source.translate_to && (() => {
          const isDeepseek = source.translator === 'deepseek';
          const cost = source.translation_cost_cny;
          const deepseekLabel = (cost != null && cost > 0)
            ? `DeepSeek Translate: ¥${cost.toFixed(2)} (7d)`
            : 'DeepSeek Translate';
          const label = isDeepseek ? deepseekLabel : 'Google Translate';
          return (
            <a
              className="translate-badge-link"
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              title={label}
            >
              <img
                className={`translate-badge translate-badge--${source.translator || 'google'}`}
                src={isDeepseek ? deepseekIcon : googleTranslateIcon}
                alt={label}
              />
            </a>
          );
        })()}
      </div>
      <div className="col-resizer" onMouseDown={startColResize} />
      <div className={`source-latest ${!latestTitle ? 'no-data' : ''}`}>
        {hasError && source.last_error
          ? (source.last_error.startsWith('Refresh timed out')
              ? <TimeoutStatus source={source} />
              : source.last_error)
          : latestTitle
            ? (() => {
                const rel = formatPubDateRelative(source.latest_pub_date);
                return (
                  <>
                    {rel && <span className={`pubdate-badge pubdate-badge--${rel.tier}`}>{rel.label}</span>}
                    <span className="latest-title-link" onClick={() => onTitleClick(source)}>{latestTitle}</span>
                  </>
                );
              })()
            : ts ? '- Empty -' : 'Never fetched'}
      </div>
      <div className="source-count">{source.feed_article_count || 0} articles</div>
      <div className="col-resizer" onMouseDown={startColResize} />
      <div className="source-time">
        {(busy || isRefreshing)
          ? <span className="source-refreshing">
              {parseProgress && parseProgress.total > 0
                ? `Refreshing ${parseProgress.done}/${parseProgress.total}`
                : 'Refreshing'}
              <span className="dot-pulse"><span>.</span><span>.</span><span>.</span></span>
            </span>
          : isQueued
            ? <span className="source-refreshing">
                Waiting<span className="dot-pulse"><span>.</span><span>.</span><span>.</span></span>
              </span>
            : <>Last Fetch: {elapsed(ts)}</>}
      </div>
      <div className="source-actions">
        {feedback && <span key={feedbackKey} className="action-feedback">{feedback}</span>}
        {!isDisabled && <button onClick={handleCopyRSS} className="row-btn" title="Copy RSS feed URL">Copy RSS</button>}
        {!isDisabled && <button onClick={handleAction(onGenerate, 'Generating')} className="row-btn" disabled={busy || isQueued} title="Generate feed now">Generate</button>}
        {!isDisabled && <button onClick={handleAction(onFlush, 'Flushing')} className="row-btn danger" disabled={busy} title="Delete all stored articles for this source">Flush</button>}
        {!isDisabled && <button onClick={handleAction(onCheck, 'Checking')} className="row-btn" disabled={busy} title="Check status">Check</button>}
        <button onClick={() => onPreview(source)} className="row-btn" title="Preview article">Preview</button>
        <button onClick={() => onEdit(source)} className="row-btn" title="Edit source">Edit</button>
        <button onClick={() => onDelete(source.id)} className="row-btn danger" title="Delete source">Delete</button>
      </div>
    </div>
  );
}

function Dashboard() {
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [editingSource, setEditingSource] = useState(null);
  const [fullPreviewSource, setFullPreviewSource] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshingSourceId, setRefreshingSourceId] = useState(null);
  const [parseProgress, setParseProgress] = useState({ done: 0, total: 0 });
  const [queuedSourceIds, setQueuedSourceIds] = useState([]);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewSource, setPreviewSource] = useState(null); // new previewSource for drawer
  const previewSourceRef = React.useRef(null);
  const previewArticleUrlRef = React.useRef('');
  const previewReqIdRef = React.useRef(0);
  const [previewLinks, setPreviewLinks] = useState([]);
  const [previewArticles, setPreviewArticles] = useState([]);  // full article data from DB
  const [previewArticleUrl, setPreviewArticleUrl] = useState('');
  const [previewContent, setPreviewContent] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const handleTitleClick = async (source) => {
    const reqId = ++previewReqIdRef.current;
    setPreviewOpen(true);
    setPreviewSource(source);
    previewSourceRef.current = source;
    previewArticleUrlRef.current = '';
    setPreviewLinks([]);
    setPreviewContent(null);
    setPreviewLoading(true);
    setPreviewArticleUrl('');

    try {
      const data = await getFeedArticles(source.id);
      if (previewReqIdRef.current !== reqId) return;
      const articles = data.articles || [];
      setPreviewArticles(articles);
      // Map DB articles to the same shape the dropdown expects
      const links = articles.map(a => ({ url: a.url, title: a.title }));
      setPreviewLinks(links);

      if (articles.length > 0) {
        const first = articles[0];
        previewArticleUrlRef.current = first.url;
        setPreviewArticleUrl(first.url);
        // Display directly from DB — no re-parsing needed
        setPreviewContent({
          title: first.title,
          date: first.pub_date,
          main_image: toAbsoluteBackendUrl(first.main_image),
          content: absolutizeArticleHtml(first.content),
        });
      }
    } catch (err) {
      if (previewReqIdRef.current !== reqId) return;
      console.error('Preview error:', err);
    } finally {
      if (previewReqIdRef.current === reqId) {
        setPreviewLoading(false);
      }
    }
  };

  const handlePreviewArticleChange = (e) => {
    const url = e.target.value;
    previewArticleUrlRef.current = url;
    setPreviewArticleUrl(url);
    const article = previewArticles.find(a => a.url === url);
    if (article) {
      setPreviewContent({
        title: article.title,
        date: article.pub_date,
        main_image: toAbsoluteBackendUrl(article.main_image),
        content: absolutizeArticleHtml(article.content),
      });
    }
  };

  const closePreview = () => {
    previewSourceRef.current = null;
    previewArticleUrlRef.current = '';
    setPreviewOpen(false);
    setPreviewSource(null);
    setPreviewLinks([]);
    setPreviewArticles([]);
    setPreviewContent(null);
  };

  const fetchSources = async (silent = false, fetchUsage = true) => {
    try {
      if (!silent) setLoading(true);
      const data = await getFeedSources();
      let merged = data;
      if (fetchUsage) {
        try {
          const usage = await getTranslationUsage(7);
          const bySource = usage.by_source || {};
          merged = data.map(s => ({
            ...s,
            translation_cost_cny: bySource[s.id] ? bySource[s.id].cost_cny : null,
          }));
        } catch { /* usage is best-effort; show sources without cost */ }
      } else {
        setSources(prev => {
          const prevCosts = new Map(prev.map(p => [p.id, p.translation_cost_cny]));
          return data.map(s => ({
            ...s,
            translation_cost_cny: prevCosts.get(s.id) ?? null,
          }));
        });
      }
      if (fetchUsage) {
        setSources(merged);
      }
      // If preview pane is open, reload its articles with fresh data
      const openSource = previewSourceRef.current;
      if (openSource) {
        const updated = data.find(s => s.id === openSource.id);
        if (updated) {
          setPreviewSource(updated);
          try {
            const aData = await getFeedArticles(updated.id);
            const articles = aData.articles || [];
            setPreviewArticles(articles);
            setPreviewLinks(articles.map(a => ({ url: a.url, title: a.title })));
            
            // Maintain current selection if possible, otherwise fallback to first
            const currentUrl = previewArticleUrlRef.current;
            const stillExists = articles.find(a => a.url === currentUrl);
            
            if (stillExists) {
              previewArticleUrlRef.current = stillExists.url;
              setPreviewArticleUrl(stillExists.url);
              setPreviewContent({ 
                title: stillExists.title, 
                date: stillExists.pub_date, 
                main_image: toAbsoluteBackendUrl(stillExists.main_image), 
                content: absolutizeArticleHtml(stillExists.content) 
              });
            } else if (articles.length > 0) {
              const first = articles[0];
              previewArticleUrlRef.current = first.url;
              setPreviewArticleUrl(first.url);
              setPreviewContent({ 
                title: first.title, 
                date: first.pub_date, 
                main_image: toAbsoluteBackendUrl(first.main_image), 
                content: absolutizeArticleHtml(first.content) 
              });
            } else {
              setPreviewContent(null);
            }
          } catch { /* ignore */ }
        }
      }
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchSources(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll refresh status while refreshing to track which source is active
  useEffect(() => {
    if (!refreshing) { setRefreshingSourceId(null); setQueuedSourceIds([]); return; }
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const status = await getRefreshStatus();
          if (!cancelled) {
            setRefreshingSourceId(status.current_source_id);
            setParseProgress({ done: status.parse_done || 0, total: status.parse_total || 0 });
            setQueuedSourceIds(status.queued_source_ids || []);
            await fetchSources(true, false);
            if (!status.refreshing) {
              setRefreshing(false);
              setParseProgress({ done: 0, total: 0 });
              setQueuedSourceIds([]);
              await fetchSources(true, true);
              break;
            }
          }
        } catch { /* ignore */ }
        await new Promise(r => setTimeout(r, 2000));
      }
    };
    poll();
    return () => { cancelled = true; };
  }, [refreshing]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDelete = async (id) => {
    if (!window.confirm('Are you sure you want to delete this source?')) return;
    try { await deleteFeedSource(id); fetchSources(); }
    catch (err) { setError(`Delete failed: ${err.message}`); }
  };

  const handleCheck = async (id) => {
    const name = sources.find(s => s.id === id)?.name || `#${id}`;
    try { await checkFeedSource(id); fetchSources(); }
    catch (err) { setError(`Check failed for "${name}": ${err.message}`); }
  };

  const handleFlush = async (id) => {
    const name = sources.find(s => s.id === id)?.name || `#${id}`;
    try { await flushFeedArticles(id); fetchSources(); }
    catch (err) { setError(`Flush failed for "${name}": ${err.message}`); }
  };

  const handleGenerate = async (id) => {
    const name = sources.find(s => s.id === id)?.name || `#${id}`;
    try {
      await generateFeed(id);
      setRefreshing(true); // triggers polling to track background progress
    }
    catch (err) { setError(`Generate failed for "${name}": ${err.message}`); }
  };

  const handleRefreshAll = async () => {
    try {
      setRefreshing(true);
      await refreshAllFeeds();
      await fetchSources(true);
    }
    catch (err) { setError(`Refresh All failed: ${err.message}`); }
    // Keep polling until backend reports refresh complete.
  };

  const handleToggleEnabled = async (id) => {
    try { await toggleFeedSourceEnabled(id); fetchSources(); }
    catch (err) { setError(`Toggle failed: ${err.message}`); }
  };

  const handleEdit = (source) => setEditingSource(source);

  const groupedSources = useMemo(() => {
    return sources.reduce((acc, source) => {
      const cat = source.category_name || 'Uncategorized';
      if (!acc[cat]) acc[cat] = [];
      acc[cat].push(source);
      return acc;
    }, {});
  }, [sources]);

  if (showAdd) {
    return <AddWebsite onCancel={() => setShowAdd(false)} onSave={() => { setShowAdd(false); fetchSources(); }} />;
  }

  if (editingSource) {
    return (
      <AddWebsite
        initialSource={editingSource}
        onCancel={() => setEditingSource(null)}
        onSave={() => { setEditingSource(null); fetchSources(); }}
      />
    );
  }

  if (fullPreviewSource) {
    return (
      <AddWebsite
        initialSource={fullPreviewSource}
        initialStep={3}
        onCancel={() => setFullPreviewSource(null)}
        onSave={() => { setFullPreviewSource(null); fetchSources(); }}
      />
    );
  }

  if (loading) return <div className="dashboard-loading">Loading...</div>;

  return (
    <>
      <div className="dashboard-header">
        <span className="dashboard-title">Feed Sources</span>
        <div className="dashboard-header-actions">
          <button onClick={handleRefreshAll} className={`btn ${refreshing ? 'btn-refreshing' : ''}`} disabled={refreshing}>
            {refreshing ? <><span>Refreshing</span><span className="dot-pulse"><span>.</span><span>.</span><span>.</span></span></> : 'Refresh All'}
          </button>
<button onClick={() => setShowAdd(true)} className="btn btn-accent">+ Add Website</button>
        </div>
      </div>

      {error && (
        <div className="error-message">
          <span>{error}</span>
          <button className="error-close-btn" onClick={() => setError(null)}>&times;</button>
        </div>
      )}

      {sources.length === 0 ? (
        <div className="empty-state">No feed sources yet. Click + Add Website to get started.</div>
      ) : (
        Object.entries(groupedSources).map(([category, catSources]) => (
          <CollapsibleCategory
            key={category}
            category={category}
            sources={catSources}
            onCheck={handleCheck}
            onDelete={handleDelete}
            onEdit={handleEdit}
            onFlush={handleFlush}
            onGenerate={handleGenerate}
            onPreview={setFullPreviewSource}
            onTitleClick={handleTitleClick}
            onToggleEnabled={handleToggleEnabled}
            refreshingSourceId={refreshingSourceId}
            parseProgress={parseProgress}
            queuedSourceIds={queuedSourceIds}
          />
        ))
      )}

      {previewOpen && (
        <div className="preview-pane-overlay" onClick={closePreview}>
          <div className="preview-pane" onClick={e => e.stopPropagation()}>
            <div className="preview-pane-header">
              <div className="preview-pane-title">
                <h3>{previewSource?.name || 'Preview'}</h3>
                <a href={previewSource?.url} target="_blank" rel="noopener noreferrer" className="preview-source-link">
                  {previewSource?.url}
                </a>
              </div>
              <div className="preview-pane-actions" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <button 
                  className="row-btn" 
                  style={{ fontSize: '12px', padding: '3px 8px' }}
                  onClick={() => { closePreview(); handleEdit(previewSource); }}
                >
                  Edit Source
                </button>
                <button className="preview-close-btn" onClick={closePreview}>&times;</button>
              </div>
            </div>            
            <div className="preview-pane-controls">
              <label>Article:</label>
              <select value={previewArticleUrl} onChange={handlePreviewArticleChange} disabled={previewLoading}>
                {previewLinks.map((l, i) => (
                  <option key={i} value={l.url}>{l.title || l.url}</option>
                ))}
                {previewLinks.length === 0 && <option>No links found</option>}
              </select>
            </div>

            {previewContent && !previewLoading && (
              <div className="preview-article-header">
                <h1>{previewContent.title}</h1>
                {previewContent.date && <div className="preview-date">{previewContent.date}</div>}
                {previewContent.main_image && (
                  <img src={previewContent.main_image} alt="" className="preview-main-image" />
                )}
              </div>
            )}
            <div className="preview-pane-content">
              {previewLoading ? (
                <div className="preview-loading">Loading article content...</div>
              ) : previewContent ? (
                <div
                  className="preview-body content-preview rss-content"
                  dangerouslySetInnerHTML={{ __html: previewContent.description || previewContent.content || '' }}
                />
              ) : (
                <div className="preview-empty">Select an article to preview</div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default Dashboard;
