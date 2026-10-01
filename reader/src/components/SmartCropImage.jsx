import React from 'react';

// Images fade in once decoded instead of popping in row by row. data-loaded is set
// on the element directly (no re-render per image); an image already in the cache
// is marked on mount, so it shows at once rather than fading on every revisit.
const markLoaded = (el) => { if (el) el.dataset.loaded = ''; };

export default function SmartCropImage({
  src,
  alt = "",
  className = "",
  style = {},
  onError,
  focalX = 50,
  focalY = 50,
}) {
  if (!src) return null;

  return (
    <img
      ref={(el) => { if (el && el.complete && el.naturalWidth) markLoaded(el); }}
      src={src}
      alt={alt}
      className={`rr-smart-img ${className}`.trim()}
      style={{
        ...style,
        objectFit: 'cover',
        objectPosition: style.objectPosition || `${focalX}% ${focalY}%`,
      }}
      onLoad={(e) => markLoaded(e.currentTarget)}
      onError={(e) => { markLoaded(e.currentTarget); if (onError) onError(e); }}
    />
  );
}
