import React, { useState, useCallback, useRef, useEffect } from "react";
import "./ElementPicker.css";

function ElementPicker({ onElementPicked, onClose, iframeElement, focusedFieldName }) {
  // Auto-activate when focusedFieldName is provided (from magnifier button)
  const [isActive, setIsActive] = useState(!!focusedFieldName);
  const handleMouseOverRef = useRef(null);
  const handleElementClickRef = useRef(null);

  // Update isActive when focusedFieldName changes
  useEffect(() => {
    setIsActive(!!focusedFieldName);
  }, [focusedFieldName]);

  // Handle deactivate/close
  const deactivate = useCallback(() => {
    setIsActive(false);
    if (typeof onClose === "function") {
      onClose();
    }
  }, [onClose]);

  // Check if an ID is article-specific (won't generalize across articles)
  const isArticleSpecificId = useCallback((id) => {
    // Match patterns like: post-123, article-456, entry-789, post_123, etc.
    return /^(post|article|entry|item|content)[-_]?\d+$/i.test(id);
  }, []);

  // Check if a selector part is semantic (has meaningful identifying class)
  const isSemanticSelector = useCallback((selector) => {
    // Semantic classes have patterns like: entry-date, GN-lbox3A, article-title, etc.
    // Generic layout containers: BH-master, BH-wrapper, BH-background, gnn-detail-cont
    const genericPatterns = /^(BH-master|BH-wrapper|BH-background|gnn-detail-cont|article|div)$/i;
    return !genericPatterns.test(selector);
  }, []);

  // Helper to get element info for tooltip
  const getElementInfo = useCallback((element) => {
    const classes = element.className ? element.className.split(' ').filter(c => c).slice(0, 2).join(' ') : '';
    const id = element.id ? `#${element.id}` : '';

    let info = element.nodeName.toLowerCase();
    if (id) info += ` ${id}`;
    if (classes) info += ` .${classes.replace(/\s+/g, '.')}`;

    return {
      classes,
      id,
      width: element.offsetWidth,
      height: element.offsetHeight,
      fullInfo: info
    };
  }, []);

  const simplifySelector = useCallback((fullPath) => {
    // Remove layout/wrapper containers to create minimal semantic path
    // Keep only elements with semantic classes
    const semanticPath = [];

    for (let i = 0; i < fullPath.length; i++) {
      const selector = fullPath[i];
      // Always keep elements with semantic classes or IDs
      if (selector.includes("#") || selector.includes(".")) {
        // Check if any class is semantic
        const classes = selector.split(".").slice(1);
        const hasSemanticClass = classes.some(cls => isSemanticSelector(cls));

        if (hasSemanticClass || selector.includes("#")) {
          semanticPath.push(selector);
        }
      }
    }

    // If we removed everything, keep last few elements (better to be specific than too general)
    if (semanticPath.length === 0) {
      return fullPath.slice(Math.max(0, fullPath.length - 2)).join(" > ");
    }

    return semanticPath.join(" > ");
  }, [isSemanticSelector]);

  const generateCSSSelector = useCallback((element) => {
    const fullPath = [];
    let current = element;

    // Build full path first
    while (current && current.nodeType === 1) {
      let selector = current.nodeName.toLowerCase();

      // Include non-article-specific IDs
      if (current.id && !isArticleSpecificId(current.id)) {
        selector += `#${current.id}`;
        fullPath.unshift(selector);
        break;
      }

      // Add classes, excluding the picker highlight class
      if (current.className && typeof current.className === "string") {
        const classes = current.className
          .split(/\s+/)
          .filter(c => c && c !== "element-picker-highlight");
        if (classes.length) {
          selector += "." + classes.join(".");
        }
      }

      fullPath.unshift(selector);
      current = current.parentNode;

      if (current && current.nodeName.toLowerCase() === "body") {
        break;
      }
    }

    // Step 1: Remove nth-child selectors for cross-article compatibility
    const withoutNthChild = fullPath.map(sel => sel.replace(/:\w+-child\(\d+\)/g, ""));
    const cleanPath = withoutNthChild.filter(sel => sel.length > 0);

    // Step 2: Simplify to keep only semantic selectors
    // This removes generic layout containers and keeps meaningful class-based selectors
    const optimizedPath = simplifySelector(cleanPath);

    // Return site-wide reusable selector
    return optimizedPath;
  }, [simplifySelector, isArticleSpecificId]);

  const getTargetDocument = useCallback(() => {
    // Safe null check for iframeElement
    if (iframeElement?.contentDocument) {
      const iframeDoc = iframeElement.contentDocument;
      // Inject element picker styles into iframe if not already present
      if (!iframeDoc.getElementById("element-picker-styles")) {
        const style = iframeDoc.createElement("style");
        style.id = "element-picker-styles";
        style.textContent = `
          .element-picker-highlight {
            outline: 3px solid #ff4444 !important;
            outline-offset: -3px !important;
            background-color: rgba(255, 68, 68, 0.2) !important;
            position: relative !important;
            z-index: 2147483646 !important;
          }
          .element-picker-badge {
            font-family: sans-serif !important;
            font-size: 12px !important;
            background: #ff4444 !important;
            color: white !important;
            padding: 2px 6px !important;
            position: absolute !important;
            top: 0 !important;
            left: 0 !important;
            z-index: 2147483647 !important;
            pointer-events: none !important;
          }
        `;
        const container = iframeDoc.head || iframeDoc.body || iframeDoc.documentElement;
        if (container) {
          container.appendChild(style);
        }
      }
      return iframeDoc;
    }
    return null;
  }, [iframeElement]);

  const handleMouseOver = useCallback((e) => {
    const targetDoc = getTargetDocument();
    if (!targetDoc) return;
    
    // Ignore if mousing over the document itself or the badge
    if (e.target === targetDoc.body || e.target === targetDoc.documentElement) return;
    if (e.target.classList.contains('element-picker-badge')) return;

    // Clear previous highlights
    const highlights = targetDoc.querySelectorAll(".element-picker-highlight");
    highlights.forEach(el => {
      el.classList.remove("element-picker-highlight");
      const badge = el.querySelector(".element-picker-badge");
      if (badge) badge.remove();
    });

    // Add new highlight
    if (e.target && e.target.classList) {
      e.target.classList.add("element-picker-highlight");

      // Create and show element info badge
      try {
        const info = getElementInfo(e.target);
        const badge = targetDoc.createElement("div");
        badge.className = "element-picker-badge";
        badge.textContent = info.fullInfo;
        
        // Use document.body for badge if element can't have children
        // but for now let's try appending to the element
        e.target.appendChild(badge);
      } catch (err) {
        // Fallback for elements that don't support children
      }
    }
  }, [getTargetDocument, getElementInfo]);

  const handleElementClick = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();

    const selector = generateCSSSelector(e.target);
    if (typeof onElementPicked === "function") {
      onElementPicked(selector, focusedFieldName);
    }

    deactivate();
  }, [onElementPicked, focusedFieldName, deactivate, generateCSSSelector]);

  // Store handler refs for addEventListener/removeEventListener
  useEffect(() => {
    handleMouseOverRef.current = handleMouseOver;
    handleElementClickRef.current = handleElementClick;
  }, [handleMouseOver, handleElementClick]);

  // Manage event listener lifecycle with useEffect
  useEffect(() => {
    if (!isActive) return;

    const targetDoc = getTargetDocument();
    if (!targetDoc) {
      return;
    }

    const mouseOverHandler = (e) => handleMouseOverRef.current?.(e);
    const clickHandler = (e) => handleElementClickRef.current?.(e);

    // Use TRUE for capture phase to beat site-specific event stoppers
    targetDoc.addEventListener("mouseover", mouseOverHandler, true);
    targetDoc.addEventListener("click", clickHandler, true);
    
    if (targetDoc.body) {
      targetDoc.body.style.cursor = "crosshair";
    }

    // Cleanup on deactivation or unmount
    return () => {
      targetDoc.removeEventListener("mouseover", mouseOverHandler, true);
      targetDoc.removeEventListener("click", clickHandler, true);
      if (targetDoc.body) {
        targetDoc.body.style.cursor = "auto";
      }
      const highlights = targetDoc.querySelectorAll(".element-picker-highlight");
      highlights.forEach(el => {
        el.classList.remove("element-picker-highlight");
        const badge = el.querySelector(".element-picker-badge");
        if (badge) badge.remove();
      });
    };
  }, [isActive, getTargetDocument, iframeElement]);

  // When used with per-field magnifier buttons, don't render the button
  // Just render a hidden div to keep the event handlers active
  return (
    <div style={{ display: 'none' }}>
      {/* Event handlers are attached via useEffect */}
    </div>
  );
}

export default ElementPicker;
