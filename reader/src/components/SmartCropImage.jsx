import React from 'react';

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
      src={src}
      alt={alt}
      className={className}
      style={{
        ...style,
        objectFit: 'cover',
        objectPosition: style.objectPosition || `${focalX}% ${focalY}%`,
      }}
      onError={onError}
    />
  );
}
