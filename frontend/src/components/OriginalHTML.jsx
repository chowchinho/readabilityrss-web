import React, { useRef, useEffect, useCallback } from "react";
import "./OriginalHTML.css";

function OriginalHTML({ data, onIframeRef, highlight }) {
  const iframeRef = useRef(null);

  // Pass the iframe element to parent (App.jsx) when it's available or reloads
  const handleLoad = useCallback((e) => {
    if (iframeRef.current && onIframeRef) {
      try {
        const doc = iframeRef.current.contentDocument;
        if (doc) {
          let style = doc.getElementById('rrss-pointer-events-fix');
          if (!style) {
            style = doc.createElement('style');
            style.id = 'rrss-pointer-events-fix';
            style.textContent = '* { pointer-events: auto !important; }';
            doc.head?.appendChild(style);
          }
        }
      } catch (err) {
        // Sandboxed cross-origin iframe does not permit contentDocument access
      }
      onIframeRef(iframeRef.current);
    }
  }, [onIframeRef]);

  // Also call it in useEffect to ensure it's passed on mount/data change
  useEffect(() => {
    if (iframeRef.current && onIframeRef) {
      onIframeRef(iframeRef.current);
    }
  }, [onIframeRef, data.original_html]);

  return (
    <div className={`original-html-container ${highlight ? 'picker-active' : ''}`}>
      <h2>Original HTML</h2>
      <div className="panel-body">
        <p className="url-row">
          <strong>URL:</strong>
          <span className="url-value">{data.url}</span>
          {data.url && (
            <a
              href={data.url}
              target="_blank"
              rel="noopener noreferrer"
              className="url-open-btn"
              title="Open in new tab"
              aria-label="Open URL in new tab"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                <polyline points="15 3 21 3 21 9"></polyline>
                <line x1="10" y1="14" x2="21" y2="3"></line>
              </svg>
            </a>
          )}
        </p>
        <p>
          <strong>Title:</strong> {data.title || "(no title)"}
        </p>
        <div className="iframe-wrapper">
          <iframe
            ref={iframeRef}
            onLoad={handleLoad}
            srcDoc={data.original_html || '<p>No HTML content available</p>'}
            sandbox="allow-popups"
            referrerPolicy="no-referrer"
            title="Rendered HTML content"
            className="content-iframe"
          />
        </div>
      </div>
    </div>
  );
}

export default OriginalHTML;
