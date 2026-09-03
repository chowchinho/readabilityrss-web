import React, { useState, useEffect, useCallback } from 'react';
import { getCategories, createCategory, discoverLinks, discoverLinksWithSelector, createFeedSource, updateFeedSource, parseURL } from '../api';
import OriginalHTML from './OriginalHTML';
import FieldsPreview from './FieldsPreview';
import './AddWebsite.css';

function AddWebsite({ onCancel, onSave, initialSource, initialStep }) {
  const editMode = !!initialSource;

  const [step, setStep] = useState(initialStep || (editMode ? 2 : 1));
  const [url, setUrl] = useState(initialSource?.url || '');
  const [name, setName] = useState(initialSource?.name || '');
  const [categories, setCategories] = useState([]);
  const [selectedCategory, setSelectedCategory] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [discoveredData, setDiscoveredData] = useState(null);
  const [selectedLinks, setSelectedLinks] = useState(new Set());
  const [itemSelector, setItemSelector] = useState(initialSource?.item_selector || '');
  const [linkSelector, setLinkSelector] = useState(initialSource?.link_selector || 'a');
  const [excludeSelector, setExcludeSelector] = useState(initialSource?.exclude_selector || '');
  const [negativeKeywords, setNegativeKeywords] = useState(initialSource?.negative_keywords || '');
  const [detectedLanguage, setDetectedLanguage] = useState(initialSource?.detected_language || null);
  const [translateEnabled, setTranslateEnabled] = useState(!!initialSource?.translate_to);
  const [translator, setTranslator] = useState(initialSource?.translator || 'google');

  // Step 3: Article preview state
  const [previewArticleUrl, setPreviewArticleUrl] = useState('');
  const [parseResults, setParseResults] = useState(null);
  const [parseOverrides, setParseOverrides] = useState(() => {
    const o = {};
    if (initialSource?.content_selector) o.content_selector = initialSource.content_selector;
    if (initialSource?.title_selector) o.title_selector = initialSource.title_selector;
    if (initialSource?.date_selector) o.date_selector = initialSource.date_selector;
    if (initialSource?.image_selector) o.image_selector = initialSource.image_selector;
    if (initialSource?.content_exclude_selector) o.content_exclude_selector = initialSource.content_exclude_selector;
    return o;
  });
  const [iframeElement, setIframeElement] = useState(null);
  const [pickerActive, setPickerActive] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [useParseDate, setUseParseDate] = useState(!!initialSource?.use_parse_date);

  const fetchCategories = useCallback(async () => {
    try {
      const data = await getCategories();
      setCategories(data);
      if (editMode && initialSource?.category_id) {
        setSelectedCategory(initialSource.category_id.toString());
      } else if (data.length > 0) {
        setSelectedCategory(data[0].id.toString());
      } else {
        setSelectedCategory('new');
      }
    } catch (err) {
      console.error(err);
    }
  }, [editMode, initialSource]);

  useEffect(() => {
    fetchCategories();
  }, [fetchCategories]);

  const handleDiscover = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (!url) return;

    setLoading(true);
    setError(null);
    try {
      let res;
      const isRssPattern = itemSelector && /\(RSS|\(Atom/i.test(itemSelector);
      if (itemSelector && !isRssPattern) {
        res = await discoverLinksWithSelector(url, itemSelector, linkSelector, excludeSelector);
      } else {
        res = await discoverLinks(url);
      }

      setDiscoveredData(res);
      const lang = (res.language || 'en').toLowerCase();
      setDetectedLanguage(lang);
      if (!editMode) {
        if (lang === 'ja' || lang.startsWith('ja-')) {
          setTranslateEnabled(true);
        } else {
          setTranslateEnabled(false);
        }
      }
      const sourceDomain = new URL(url).hostname.replace(/^www\./, '');
      const sameDomainLinks = res.links.filter(l => {
        try {
          const linkDomain = new URL(l.url).hostname.replace(/^www\./, '');
          return linkDomain === sourceDomain;
        } catch { return false; }
      });
      setSelectedLinks(new Set(sameDomainLinks.map(l => l.url)));
      if (res.site_name && !name) {
        setName(res.site_name);
      }
      if (!editMode) setStep(2);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const toggleLink = (linkUrl) => {
    const newSet = new Set(selectedLinks);
    if (newSet.has(linkUrl)) newSet.delete(linkUrl);
    else newSet.add(linkUrl);
    setSelectedLinks(newSet);
  };

  const toggleAll = () => {
    if (selectedLinks.size === discoveredData.links.length) {
      setSelectedLinks(new Set());
    } else {
      setSelectedLinks(new Set(discoveredData.links.map(l => l.url)));
    }
  };

  // Step 3: Parse an article for preview
  const parseArticle = async (articleUrl, overrides = {}) => {
    setParsing(true);
    setError(null);
    try {
      const result = await parseURL(articleUrl, overrides);
      setParseResults(result);
      setPreviewArticleUrl(articleUrl);
    } catch (err) {
      setError(err.message);
    } finally {
      setParsing(false);
    }
  };

  const handleGoToPreview = async () => {
    let data = discoveredData;
    let linksForPreview;

    if (!data) {
      setLoading(true);
      setError(null);
      try {
        data = itemSelector
          ? await discoverLinksWithSelector(url, itemSelector, linkSelector, excludeSelector)
          : await discoverLinks(url);
        setDiscoveredData(data);
        const sourceDomain = new URL(url).hostname.replace(/^www\./, '');
        const sameDomain = data.links.filter(l => {
          try { return new URL(l.url).hostname.replace(/^www\./, '') === sourceDomain; }
          catch { return false; }
        });
        const autoSelected = new Set((sameDomain.length > 0 ? sameDomain : data.links).map(l => l.url));
        setSelectedLinks(autoSelected);
        linksForPreview = data.links.filter(l => autoSelected.has(l.url));
      } catch (err) {
        setError(err.message);
        setLoading(false);
        return;
      } finally {
        setLoading(false);
      }
    } else {
      linksForPreview = data.links.filter(l => selectedLinks.has(l.url));
    }

    if (linksForPreview.length === 0) return;
    setParseResults(null);
    setStep(3);
    parseArticle(linksForPreview[0].url, parseOverrides);
  };

  // Auto-trigger preview when entering directly at step 3
  useEffect(() => {
    if (initialStep === 3 && editMode) {
      handleGoToPreview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleArticleChange = (e) => {
    const newUrl = e.target.value;
    setParseResults(null);
    setIframeElement(null);
    parseArticle(newUrl, parseOverrides);
  };

  const handleOverride = async (overrides) => {
    const merged = { ...parseOverrides, ...overrides };
    setParsing(true);
    setError(null);
    try {
      const result = await parseURL(previewArticleUrl, merged);
      setParseOverrides(merged);
      setParseResults(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setParsing(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    try {
      let finalCategoryId = selectedCategory;
      if (selectedCategory === 'new') {
        if (!newCategory.trim()) {
          setError('Please enter a name for the new category.');
          setLoading(false);
          return;
        }
        const cat = await createCategory(newCategory.trim());
        finalCategoryId = cat.id.toString();
      }

      const parsedCatId = parseInt(finalCategoryId, 10);
      if (isNaN(parsedCatId)) {
        setError('Please select a valid category.');
        setLoading(false);
        return;
      }

      const payload = {
        name: name || url,
        url,
        category_id: parsedCatId,
        item_selector: (() => {
          if (itemSelector && !/\(RSS|\(Atom/i.test(itemSelector)) return itemSelector;
          const s = discoveredData?.selector_used || '';
          if (/\(RSS|\(Atom/i.test(s)) return null;
          return s.replace(/\s*>\s*a$|\s+a$/, '') || null;
        })(),
        link_selector: linkSelector,
        exclude_selector: excludeSelector || null,
        detected_language: detectedLanguage || null,
        translate_to: translateEnabled ? '1' : null,
        translator: translateEnabled ? translator : 'google',
        content_selector: parseOverrides.content_selector || null,
        title_selector: parseOverrides.title_selector || null,
        date_selector: useParseDate ? null : (parseOverrides.date_selector || null),
        image_selector: parseOverrides.image_selector || null,
        content_exclude_selector: parseOverrides.content_exclude_selector || null,
        negative_keywords: negativeKeywords.trim() || null,
        use_parse_date: useParseDate,
      };

      if (discoveredData) {
        const allUrls = discoveredData.links.map(l => l.url);
        const excludedUrls = allUrls.filter(u => !selectedLinks.has(u));
        if (excludedUrls.length > 0) payload.excluded_urls = excludedUrls;
      }

      if (editMode) {
        await updateFeedSource(initialSource.id, payload);
      } else {
        await createFeedSource(payload);
      }
      onSave();
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  // Get selected links as array for the dropdown.
  // Also hide titles matching the Negative Keywords list (same rule the backend
  // applies on the next fetch), so the preview reflects what will land in the feed.
  const negativeKeywordList = negativeKeywords
    .split(',')
    .map(k => k.trim().toLowerCase())
    .filter(Boolean);
  const selectedLinksArray = discoveredData
    ? discoveredData.links.filter(l => {
        if (!selectedLinks.has(l.url)) return false;
        if (negativeKeywordList.length === 0) return true;
        const title = (l.title || '').toLowerCase();
        return !negativeKeywordList.some(kw => title.includes(kw));
      })
    : [];

  const stepLabels = ['Discover', 'Configure', 'Preview'];

  return (
    <div className={`add-website-container ${step === 3 ? 'add-website-fullwidth' : ''}`}>
      <h2 className="add-website-title">{editMode ? 'Edit Source' : 'Add New Website'}</h2>

      <div className="step-indicator">
        {stepLabels.map((label, i) => {
          const n = i + 1;
          const cls = n < step ? 'complete' : n === step ? 'active' : '';
          return (
            <React.Fragment key={n}>
              {i > 0 && <div className="step-connector" />}
              <div className={`step ${cls}`}>
                <div className="step-num">{n < step ? '✓' : n}</div>
                <span className="step-label">{label}</span>
              </div>
            </React.Fragment>
          );
        })}
      </div>

      {error && <div className="error-message">{error}</div>}

      {step === 1 && (
        <form onSubmit={handleDiscover} className="add-form">
          <div className="form-group">
            <label>Index URL / Archive URL / RSS Link</label>
            <input
              type="url"
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="https://example.com/news"
              required
            />
          </div>

          <div className="form-actions">
            <button type="button" onClick={onCancel} className="cancel-btn" disabled={loading}>Cancel</button>
            <button type="submit" className="primary-btn" disabled={loading}>
              {loading ? 'Discovering...' : 'Discover Links'}
            </button>
          </div>
        </form>
      )}

      {step === 2 && (editMode || discoveredData) && (
        <div className="discovery-results">
          {/* URL field */}
          <div className="form-group">
            <label>URL</label>
            {editMode ? (
              <div className="url-edit-row">
                <input
                  type="url"
                  value={url}
                  onChange={e => setUrl(e.target.value)}
                />
                <button onClick={handleDiscover} disabled={loading} className="secondary-btn">
                  {loading ? 'Loading...' : 'Update'}
                </button>
              </div>
            ) : (
              <div className="url-display">{url}</div>
            )}
          </div>

          {/* Category selector */}
          <div className="form-group">
            <label>Category</label>
            <select
              value={selectedCategory}
              onChange={e => setSelectedCategory(e.target.value)}
            >
              {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              <option value="new">+ Add new category</option>
            </select>
          </div>

          {selectedCategory === 'new' && (
            <div className="form-group">
              <label>New Category Name</label>
              <input
                type="text"
                value={newCategory}
                onChange={e => setNewCategory(e.target.value)}
              />
            </div>
          )}

          <div className="form-group">
            <label>Website Name</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. TechCrunch"
            />
          </div>

          {discoveredData && (
            <>
              <div className="info-bar">
                Found {discoveredData.total_found} links · selector: <code>{discoveredData.selector_used || 'auto-detected'}</code>
                {detectedLanguage && (
                  <span className="language-badge">{detectedLanguage} detected</span>
                )}
              </div>

              <div className="links-list-container">
                <div className="links-header">
                  <label>
                    <input
                      type="checkbox"
                      checked={selectedLinks.size === discoveredData.links.length && discoveredData.links.length > 0}
                      onChange={toggleAll}
                    />
                    Select All · {selectedLinks.size} selected
                  </label>
                </div>
                <div className="links-list">
                  {discoveredData.links.map((link, idx) => (
                    <div key={idx} className={`link-item ${!selectedLinks.has(link.url) ? 'link-excluded' : ''}`}>
                      <input
                        type="checkbox"
                        checked={selectedLinks.has(link.url)}
                        onChange={() => toggleLink(link.url)}
                      />
                      <div className="link-details">
                        <div className="link-title">{link.title || 'No Title'}</div>
                        <div className="link-url">{link.url}</div>
                      </div>
                    </div>
                  ))}
                  {discoveredData.links.length === 0 && (
                    <div className="no-links">No valid article links found. Try manual selectors.</div>
                  )}
                </div>
              </div>

              <div className="selector-override">
                <h4>Override Selectors (Optional)</h4>
                <div className="selector-inputs">
                  <input
                    type="text"
                    placeholder="Item Selector (e.g. .article-card)"
                    value={itemSelector}
                    onChange={e => setItemSelector(e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="Link Selector (e.g. a)"
                    value={linkSelector}
                    onChange={e => setLinkSelector(e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="Exclude (e.g. .ad-banner)"
                    value={excludeSelector}
                    onChange={e => setExcludeSelector(e.target.value)}
                  />
                  <button onClick={handleDiscover} disabled={loading} className="secondary-btn">
                    Re-detect
                  </button>
                </div>
                <div className="negative-keywords-row">
                  <input
                    type="text"
                    placeholder="Negative Keywords (comma-separated, matches article titles)"
                    value={negativeKeywords}
                    onChange={e => setNegativeKeywords(e.target.value)}
                  />
                  <small className="negative-keywords-hint">
                    Titles containing any of these (case-insensitive) will be excluded on the next fetch. Already-stored articles are not affected.
                  </small>
                </div>
              </div>
            </>
          )}

          <div className={`translate-option ${translateEnabled ? 'has-sub-selector' : ''}`}>
            <div className="translate-text">
              <div className="translate-label">Enable Article Translation</div>
              <div className="translate-hint">
                Articles will be translated into the target language configured in Options.
                {detectedLanguage && (detectedLanguage === 'ja' || detectedLanguage.startsWith('ja-')) &&
                  <span className="language-badge" style={{marginLeft: 6}}>ja detected</span>}
              </div>
            </div>
            <button
              type="button"
              className={`translate-toggle-btn ${translateEnabled ? 'on' : ''}`}
              onClick={() => setTranslateEnabled(v => !v)}
              aria-label="Toggle translation"
            />
          </div>

          {translateEnabled && (
            <div className="translator-selector-row">
              <label htmlFor="translator-select">Translation Service</label>
              <select
                id="translator-select"
                value={translator}
                onChange={e => setTranslator(e.target.value)}
              >
                <option value="google">Google Translate</option>
                <option value="deepseek">DeepSeek Translate (Natural HK Register)</option>
              </select>
            </div>
          )}

          {!discoveredData && editMode && (
            <div className="load-links-hint">Click "Update" to load links for this URL.</div>
          )}

          <div className="form-actions">
            <button
              onClick={editMode ? onCancel : () => setStep(1)}
              className="secondary-btn"
              disabled={loading}
            >
              {editMode ? 'Cancel' : 'Back'}
            </button>
            {editMode ? (
              <>
                <button onClick={handleGoToPreview} className="secondary-btn" disabled={loading || !url}>
                  Preview Article
                </button>
                <button onClick={handleSave} className="primary-btn" disabled={loading}>
                  {loading ? 'Saving...' : 'Save Source'}
                </button>
              </>
            ) : (
              <button
                onClick={handleGoToPreview}
                className="primary-btn"
                disabled={loading || !discoveredData || selectedLinks.size === 0}
              >
                Preview Article
              </button>
            )}
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="article-preview-step">
          {/* Article selector — matches prototype form-group pattern */}
          <div className="form-group" style={{marginBottom: 16}}>
            <label>Preview Article</label>
            <select value={previewArticleUrl} onChange={handleArticleChange} disabled={parsing}>
              {selectedLinksArray.map((link, idx) => (
                <option key={idx} value={link.url}>
                  {link.title || link.url}
                </option>
              ))}
            </select>
          </div>

          {parsing && (
            <div className="parsing-indicator">Parsing article...</div>
          )}

          {parseResults && !parsing && (
            <div className="article-preview-layout">
              <OriginalHTML
                data={parseResults}
                onIframeRef={setIframeElement}
                highlight={pickerActive}
              />
              <FieldsPreview
                data={parseResults}
                onOverride={handleOverride}
                iframeElement={iframeElement}
                onPickerStateChange={setPickerActive}
                parseOverrides={parseOverrides}
                useParseDate={useParseDate}
                onUseParseDateChange={setUseParseDate}
              />
            </div>
          )}

          <div className="form-actions">
            <button onClick={() => { setStep(2); setParseResults(null); setIframeElement(null); }} className="secondary-btn" disabled={loading}>
              Back
            </button>
            <button onClick={handleSave} className="primary-btn" disabled={loading || parsing}>
              {loading ? 'Saving...' : 'Save Source'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default AddWebsite;
