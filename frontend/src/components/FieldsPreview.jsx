import React, { useState, useCallback, useMemo } from "react";
import DOMPurify from "dompurify";
import "./FieldsPreview.css";
import ElementPicker from "./ElementPicker";

const FIELD_TO_OVERRIDE_MAP = {
  title: "title_selector",
  content: "content_selector",
  date: "date_selector",
  image: "image_selector",
  exclude: "content_exclude_selector",
};

const Icon = ({ type }) => {
  switch (type) {
    case 'magnifier':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
      );
    case 'info':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="16" x2="12" y2="12"></line>
          <line x1="12" y1="8" x2="12.01" y2="8"></line>
        </svg>
      );
    case 'warning':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
          <line x1="12" y1="9" x2="12" y2="13"></line>
          <line x1="12" y1="17" x2="12.01" y2="17"></line>
        </svg>
      );
    case 'check':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
      );
    default:
      return null;
  }
};

function FieldsPreview({ data, onOverride, iframeElement, onPickerStateChange, parseOverrides = {}, useParseDate = false, onUseParseDateChange }) {
  const [focusedField, setFocusedField] = useState(null);
  const [appliedFields, setAppliedFields] = useState({});
  const [selectors, setSelectors] = useState({
    title: "",
    content: "",
    date: "",
    image: "",
    exclude: "",
  });

  const handleFocus = useCallback((fieldName) => {
    setFocusedField(fieldName);
    if (onPickerStateChange) onPickerStateChange(true);
  }, [onPickerStateChange]);

  const handleSelectorChange = useCallback((fieldName, value) => {
    setSelectors(prev => ({
      ...prev,
      [fieldName]: value,
    }));
  }, []);

  const handleElementPicked = useCallback((selector, fieldName) => {
    if (!fieldName || !selector) return;
    // For multi-value fields, auto-append picked selector as a new chip
    if (fieldName === "content" || fieldName === "exclude") {
      const overrideKey = FIELD_TO_OVERRIDE_MAP[fieldName];
      const current = parseOverrides[overrideKey] || "";
      const existing = current.split(",").map(s => s.trim()).filter(Boolean);
      if (existing.includes(selector)) return;
      const next = existing.concat(selector).join(", ");
      onOverride({ [overrideKey]: next });
      return;
    }
    setSelectors(prev => ({
      ...prev,
      [fieldName]: selector,
    }));
  }, [parseOverrides, onOverride]);

  const handleAddChip = useCallback((fieldName) => {
    const value = (selectors[fieldName] || "").trim();
    if (!value) return;
    const overrideKey = FIELD_TO_OVERRIDE_MAP[fieldName];
    const current = parseOverrides[overrideKey] || "";
    const existing = current.split(",").map(s => s.trim()).filter(Boolean);
    // Allow user to paste a comma-separated list and split it into chips
    const incoming = value.split(",").map(s => s.trim()).filter(Boolean);
    const merged = [...existing];
    for (const sel of incoming) {
      if (!merged.includes(sel)) merged.push(sel);
    }
    if (merged.length === existing.length) {
      setSelectors(prev => ({ ...prev, [fieldName]: "" }));
      return;
    }
    onOverride({ [overrideKey]: merged.join(", ") });
    setAppliedFields(prev => ({ ...prev, [fieldName]: true }));
    setTimeout(() => {
      setAppliedFields(prev => ({ ...prev, [fieldName]: false }));
    }, 1000);
    setSelectors(prev => ({ ...prev, [fieldName]: "" }));
  }, [selectors, parseOverrides, onOverride]);

  const handleRemoveChip = useCallback((fieldName, chipValue) => {
    const overrideKey = FIELD_TO_OVERRIDE_MAP[fieldName];
    const current = parseOverrides[overrideKey] || "";
    const remaining = current.split(",").map(s => s.trim()).filter(Boolean).filter(s => s !== chipValue);
    onOverride({ [overrideKey]: remaining.length ? remaining.join(", ") : null });
  }, [parseOverrides, onOverride]);

  const getChips = useCallback((fieldName) => {
    const overrideKey = FIELD_TO_OVERRIDE_MAP[fieldName];
    const current = parseOverrides[overrideKey] || "";
    return current.split(",").map(s => s.trim()).filter(Boolean);
  }, [parseOverrides]);

  const handlePickerClose = useCallback(() => {
    setFocusedField(null);
    if (onPickerStateChange) onPickerStateChange(false);
  }, [onPickerStateChange]);

  const handleApply = useCallback((fieldName) => {
    const selectorValue = selectors[fieldName];
    if (!selectorValue.trim()) {
      return;
    }

    const overrideKey = FIELD_TO_OVERRIDE_MAP[fieldName];
    onOverride({ [overrideKey]: selectorValue });

    // Show success feedback
    setAppliedFields(prev => ({ ...prev, [fieldName]: true }));
    setTimeout(() => {
      setAppliedFields(prev => ({ ...prev, [fieldName]: false }));
    }, 1000);

    // Clear the selector input after applying
    setSelectors(prev => ({
      ...prev,
      [fieldName]: "",
    }));
  }, [selectors, onOverride]);

  const handleReset = useCallback((fieldName) => {
    const overrideKey = FIELD_TO_OVERRIDE_MAP[fieldName];
    onOverride({ [overrideKey]: null });
  }, [onOverride]);

  const hasCustomSelector = (fieldName) =>
    !!parseOverrides[FIELD_TO_OVERRIDE_MAP[fieldName]];

  const getActiveSelector = (fieldName) =>
    parseOverrides[FIELD_TO_OVERRIDE_MAP[fieldName]] || null;

  // Render content — images are already merged in-place by the backend
  const renderContent = useMemo(() => {
    if (!data?.description) return null;
    return data.description;
  }, [data]);

  return (
    <div className="fields-preview-container">
      <div className="fields-header">
        <h2>Parsed Fields</h2>
        {focusedField && (
          <div className="active-picker-indicator">
            🎯 Picking: <strong>{focusedField}</strong>
          </div>
        )}
      </div>

      <div className="fields-body">
      {/* Title Field */}
      <div className={`field-section ${!data.title ? "field-missing" : ""} ${appliedFields.title ? "apply-success" : ""} ${hasCustomSelector("title") ? "field-custom-selector" : ""}`}>
        <div className="field-value">
          <strong>Title{hasCustomSelector("title") ? ": (Custom CSS)" : ":"}</strong>
          <p>{data.title || "(not found)"}</p>
        </div>
        <div className="field-override">
          <label htmlFor="title-selector">CSS Selector:{getActiveSelector('title') && <><code className="active-selector-value">{getActiveSelector('title')}</code><button type="button" className="reset-selector-btn" onClick={() => handleReset('title')}>✕</button></>}</label>
          <div className="selector-input-group">
            <button
              type="button"
              onClick={() => handleFocus("title")}
              className="picker-button"
              title="Pick element for title"
              aria-label="Pick element for title"
            >
              <Icon type="magnifier" />
            </button>
            <input
              id="title-selector"
              type="text"
              data-field="title"
              value={selectors.title}
              onChange={(e) => handleSelectorChange("title", e.target.value)}
              onFocus={() => handleFocus("title")}
              placeholder="e.g., .article-title"
              className="selector-input"
            />
            <button
              type="button"
              onClick={() => handleApply("title")}
              className="apply-button"
              disabled={!selectors.title.trim()}
            >
              <Icon type="check" /> Apply
            </button>
          </div>
        </div>
      </div>

      {/* Content Field with Inline Images */}
      <div className={`field-section ${!data.description ? "field-missing" : ""} ${appliedFields.content ? "apply-success" : ""} ${hasCustomSelector("content") ? "field-custom-selector" : ""}`}>
        <div className="field-value">
          <strong>Content & Images{hasCustomSelector("content") ? ": (Custom CSS)" : ":"}</strong>
          <div className="content-preview rss-content">
            <div dangerouslySetInnerHTML={{ __html: renderContent ? DOMPurify.sanitize(renderContent, { ADD_TAGS: ['iframe', 'embed', 'video', 'audio', 'source'], ADD_ATTR: ['allow', 'allowfullscreen', 'frameborder', 'scrolling', 'target'] }) : "(not found)" }} />
          </div>
        </div>
        <div className="field-override">
          <label htmlFor="content-selector">
            CSS Selector (Container):
            {getChips('content').length > 0 && (
              <button type="button" className="reset-selector-btn" onClick={() => handleReset('content')} title="Remove all">✕ all</button>
            )}
          </label>
          {getChips('content').length > 0 && (
            <div className="selector-chip-list">
              {getChips('content').map((chip) => (
                <span key={chip} className="selector-chip">
                  <code>{chip}</code>
                  <button type="button" className="selector-chip-remove" onClick={() => handleRemoveChip('content', chip)} title="Remove this selector">✕</button>
                </span>
              ))}
            </div>
          )}
          <div className="selector-input-group">
            <button
              type="button"
              onClick={() => handleFocus("content")}
              className="picker-button"
              title="Pick element for content"
              aria-label="Pick element for content"
            >
              <Icon type="magnifier" />
            </button>
            <input
              id="content-selector"
              type="text"
              data-field="content"
              value={selectors.content}
              onChange={(e) => handleSelectorChange("content", e.target.value)}
              onFocus={() => handleFocus("content")}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddChip("content"); } }}
              placeholder="e.g., .article-content"
              className="selector-input"
            />
            <button
              type="button"
              onClick={() => handleAddChip("content")}
              className="apply-button"
              disabled={!selectors.content.trim()}
              title="Add selector to list"
            >
              + Add
            </button>
          </div>
        </div>

        {/* Nested Exclude Selector */}
        <div className="field-override" style={{ marginTop: '12px', borderTop: '1px dashed var(--border)', paddingTop: '12px' }}>
          <label htmlFor="exclude-selector">
            Exclude Elements (within container):
            {getChips('exclude').length > 0 && (
              <button type="button" className="reset-selector-btn" onClick={() => handleReset('exclude')} title="Remove all">✕ all</button>
            )}
          </label>
          {getChips('exclude').length > 0 && (
            <div className="selector-chip-list">
              {getChips('exclude').map((chip) => (
                <span key={chip} className="selector-chip">
                  <code>{chip}</code>
                  <button type="button" className="selector-chip-remove" onClick={() => handleRemoveChip('exclude', chip)} title="Remove this selector">✕</button>
                </span>
              ))}
            </div>
          )}
          <div className="selector-input-group">
            <button
              type="button"
              onClick={() => handleFocus("exclude")}
              className="picker-button"
              title="Pick element to exclude"
              aria-label="Pick element to exclude"
            >
              <Icon type="magnifier" />
            </button>
            <input
              id="exclude-selector"
              type="text"
              data-field="exclude"
              value={selectors.exclude}
              onChange={(e) => handleSelectorChange("exclude", e.target.value)}
              onFocus={() => handleFocus("exclude")}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddChip("exclude"); } }}
              placeholder="e.g., .social-share"
              className="selector-input"
            />
            <button
              type="button"
              onClick={() => handleAddChip("exclude")}
              className="apply-button"
              disabled={!selectors.exclude.trim()}
              title="Add selector to list"
            >
              + Add
            </button>
          </div>
          <p className="field-note" style={{marginTop: '6px', fontSize: '10px', color: 'var(--text-muted)'}}>
            Elements to remove (e.g. <code>.ads</code>, <code>.related</code>)
          </p>
        </div>
      </div>
      

      {/* Language Field (Read-Only) */}
      <div className="field-section">
        <div className="field-value">
          <strong>Language:</strong>
          <p>{data.language || "en"}</p>
        </div>
        <div className="field-note">(Not overridable - extracted from meta tags)</div>
      </div>

      {/* Pub Date Field */}
      <div className={`field-section ${!data.pub_date ? "field-missing" : ""} ${appliedFields.date ? "apply-success" : ""} ${hasCustomSelector("date") ? "field-custom-selector" : ""}`}>
        <div className="field-value">
          <strong>Pub Date{hasCustomSelector("date") ? ": (Custom CSS)" : ":"}</strong>
          <p>{useParseDate ? "(will use parse date)" : (data.pub_date || "(not found)")}</p>
        </div>
        <div className="field-override">
          <label className="use-parse-date-row">
            <input
              type="checkbox"
              checked={useParseDate}
              onChange={(e) => onUseParseDateChange && onUseParseDateChange(e.target.checked)}
            />
            Use parsing date as publish date
          </label>
          <label htmlFor="date-selector" className={useParseDate ? "field-override-disabled" : ""}>CSS Selector:{getActiveSelector('date') && <><code className="active-selector-value">{getActiveSelector('date')}</code><button type="button" className="reset-selector-btn" onClick={() => handleReset('date')} disabled={useParseDate}>✕</button></>}</label>
          <div className={`selector-input-group ${useParseDate ? "field-override-disabled" : ""}`}>
            <button
              type="button"
              onClick={() => handleFocus("date")}
              className="picker-button"
              title="Pick element for pub date"
              aria-label="Pick element for pub date"
              disabled={useParseDate}
            >
              <Icon type="magnifier" />
            </button>
            <input
              id="date-selector"
              type="text"
              data-field="date"
              value={selectors.date}
              onChange={(e) => handleSelectorChange("date", e.target.value)}
              onFocus={() => handleFocus("date")}
              placeholder="e.g., .publish-date"
              className="selector-input"
              disabled={useParseDate}
            />
            <button
              type="button"
              onClick={() => handleApply("date")}
              className="apply-button"
              disabled={useParseDate || !selectors.date.trim()}
            >
              <Icon type="check" /> Apply
            </button>
          </div>
        </div>
      </div>

      {/* Main Image Field */}
      <div className={`field-section ${!data.main_image ? "field-missing" : ""} ${appliedFields.image ? "apply-success" : ""} ${hasCustomSelector("image") ? "field-custom-selector" : ""}`}>
        <div className="field-value">
          <strong>Main Image{hasCustomSelector("image") ? ": (Custom CSS)" : ":"}</strong>
          {data.main_image ? (
            <div className="image-preview">
              <img src={data.main_image} alt="Main" style={{
                maxWidth: '100%',
                maxHeight: '240px',
                width: 'auto',
                height: 'auto',
                objectFit: 'contain',
              }} />
              <p className="image-url">{data.main_image}</p>
            </div>
          ) : (
            <p>(not found)</p>
          )}
        </div>
        <div className="field-override">
          <label htmlFor="image-selector">CSS Selector:{getActiveSelector('image') && <><code className="active-selector-value">{getActiveSelector('image')}</code><button type="button" className="reset-selector-btn" onClick={() => handleReset('image')}>✕</button></>}</label>
          <div className="selector-input-group">
            <button
              type="button"
              onClick={() => handleFocus("image")}
              className="picker-button"
              title="Pick element for main image"
              aria-label="Pick element for main image"
            >
              <Icon type="magnifier" />
            </button>
            <input
              id="image-selector"
              type="text"
              data-field="image"
              value={selectors.image}
              onChange={(e) => handleSelectorChange("image", e.target.value)}
              onFocus={() => handleFocus("image")}
              placeholder="e.g., .article-image"
              className="selector-input"
            />
            <button
              type="button"
              onClick={() => handleApply("image")}
              className="apply-button"
              disabled={!selectors.image.trim()}
            >
              <Icon type="check" /> Apply
            </button>
          </div>
        </div>
      </div>

      {/* Element Picker Overlay - Shows when a field is focused */}
      {focusedField && (
        <ElementPicker
          onElementPicked={handleElementPicked}
          onClose={handlePickerClose}
          iframeElement={iframeElement}
          focusedFieldName={focusedField}
        />
      )}
      </div>
    </div>
  );
}

export default FieldsPreview;
