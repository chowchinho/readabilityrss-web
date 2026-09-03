import React, { useState, useEffect, useRef } from 'react';
import SmartCropImage from './SmartCropImage';
import { API_URL, getArticleImages } from '../api';
import { getArticleImagesFromDB, saveArticleImagesToDB } from '../db';

const MAX_IMAGES_CACHE = 300;
const imagesCache = new Map();

function getCachedImages(key) {
  if (!imagesCache.has(key)) return undefined;
  const val = imagesCache.get(key);
  imagesCache.delete(key);
  imagesCache.set(key, val);
  return val;
}

function setCachedImages(key, val) {
  if (imagesCache.has(key)) {
    imagesCache.delete(key);
  } else if (imagesCache.size >= MAX_IMAGES_CACHE) {
    const firstKey = imagesCache.keys().next().value;
    imagesCache.delete(firstKey);
  }
  imagesCache.set(key, val);
}

function isIconUrl(url) {
  if (!url) return true;
  const lower = url.toLowerCase();
  if (lower.endsWith('.svg') || lower.includes('.svg?')) return true;
  const iconPatterns = ['favicon', 'avatar', 'badge', 'pixel', 'icon', 'logo', '1x1', 'spacer', 'emoji', 'tracking'];
  return iconPatterns.some(pat => lower.includes(pat));
}

function checkImageDimensions(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.src = src;
    if (img.complete) {
      resolve(img.naturalWidth >= 150 && img.naturalHeight >= 150);
    } else {
      img.onload = () => resolve(img.naturalWidth >= 150 && img.naturalHeight >= 150);
      img.onerror = () => resolve(false);
    }
  });
}

export default function ArticleCardSlideshow({
  article,
  mainImage,
  focalX = 50,
  focalY = 50,
  fallbackIconSize = 36,
  className = "",
  style = {}
}) {
  const [isVisible, setIsVisible] = useState(false);
  const initialSlide = mainImage ? { src: mainImage, focalX, focalY } : null;
  const [slides, setSlides] = useState(initialSlide ? [initialSlide] : []);

  const containerRef = useRef(null);
  const isFetchingRef = useRef(false);

  // IntersectionObserver: start slideshow when card appears inside its scroll container
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const scrollParent = el.closest('.feed-container, .sources-index-container') || null;

    const observer = new IntersectionObserver(([entry]) => {
      setIsVisible(entry.isIntersecting);
    }, { root: scrollParent, rootMargin: '50px', threshold: 0.1 });

    observer.observe(el);

    return () => {
      observer.unobserve(el);
    };
  }, []);

  // Sync mainImage prop if it changes
  useEffect(() => {
    if (mainImage) {
      const mainSlideObj = { src: mainImage, focalX, focalY };
      setSlides(prev => {
        if (!prev.some(s => s.src === mainImage)) {
          return [mainSlideObj, ...prev.filter(s => s.src !== mainImage)];
        }
        return prev;
      });
    }
  }, [mainImage, focalX, focalY]);

  // Handle lazy loading when visible on screen
  useEffect(() => {
    if (!isVisible || !article || !article.id) return;

    const imgMatches = article.content_head ? (article.content_head.match(/<img[^>]+>/gi) || []) : null;
    if (imgMatches !== null && imgMatches.length === 0 && mainImage) {
      return;
    }

    const cached = getCachedImages(article.id);
    if (cached) {
      if (cached.length > 0) {
        setSlides(cached);
      }
      return;
    }

    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    let cancelled = false;

    (async () => {
      try {
        let rawImages = [];
        try {
          const res = await getArticleImages(article.id);
          rawImages = res?.images || [];
          if (rawImages.length > 0) {
            saveArticleImagesToDB(article.id, rawImages).catch(() => {});
          }
        } catch (fetchErr) {
          try {
            const dbImages = await getArticleImagesFromDB(article.id);
            if (dbImages && dbImages.length > 0) {
              rawImages = dbImages;
            }
          } catch (dbErr) {
            // DB fallback failed
          }
          if (rawImages.length === 0) {
            throw fetchErr;
          }
        }

        const candidates = [];
        const seenUrls = new Set();

        if (mainImage && !isIconUrl(mainImage)) {
          candidates.push({ src: mainImage, focalX, focalY });
          seenUrls.add(mainImage);
        }

        for (const item of rawImages) {
          const src = item.proxy ? (item.proxy.startsWith('http') ? item.proxy : `${API_URL}${item.proxy}`) : item.original;
          if (src && !isIconUrl(src) && !seenUrls.has(src)) {
            candidates.push({
              src,
              focalX: item.focal_x ?? 50,
              focalY: item.focal_y ?? 50,
            });
            seenUrls.add(src);
          }
        }

        const sizeChecks = await Promise.all(
          candidates.map(c => checkImageDimensions(c.src))
        );

        const validSlides = candidates.filter((_, idx) => sizeChecks[idx]);

        setCachedImages(article.id, validSlides);
        if (!cancelled && validSlides.length > 0) {
          setSlides(validSlides);
        }
      } catch (err) {
        console.warn(`Failed to fetch slideshow images for article ${article.id}`, err);
      } finally {
        isFetchingRef.current = false;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isVisible, article, mainImage, focalX, focalY]);

  const baseSlide = slides[0] || (mainImage ? { src: mainImage, focalX, focalY } : null);
  const totalDuration = slides.length * 4;
  const fadeKeyframeName = `monocleOverlayFade_${slides.length}`;

  // Fixed 1.2s cross-fade transition regardless of number of slides
  const fadeInPct = ((1.2 / totalDuration) * 100).toFixed(1);
  const holdPct = ((4.0 / totalDuration) * 100).toFixed(1);
  const fadeOutPct = (((4.0 + 1.2) / totalDuration) * 100).toFixed(1);

  const dynamicKeyframeCSS = `@keyframes ${fadeKeyframeName} { 0% { opacity: 0; } ${fadeInPct}% { opacity: 1; } ${holdPct}% { opacity: 1; } ${fadeOutPct}% { opacity: 0; } 100% { opacity: 0; } }`;

  return (
    <div
      ref={containerRef}
      className={`monocle-slideshow-wrap ${className}`}
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', ...style }}
    >
      {slides.length > 1 && <style>{dynamicKeyframeCSS}</style>}

      {/* Base main image: always rendered statically at opacity 1 on first load */}
      {baseSlide ? (
        <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', opacity: 1 }}>
          <SmartCropImage
            src={baseSlide.src}
            alt=""
            focalX={baseSlide.focalX}
            focalY={baseSlide.focalY}
            style={{ width: '100%', height: '100%' }}
          />
        </div>
      ) : (
        <div className="monocle-card-image-fallback" style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span className="material-symbols-outlined" style={{ fontSize: fallbackIconSize }}>
            newspaper
          </span>
        </div>
      )}

      {/* Subsequent overlay slides (idx >= 1): fade in over base image starting after 4.0s */}
      {slides.length > 1 && slides.slice(1).map((slide, idx) => {
        const slideIndex = idx + 1;
        return (
          <div
            key={slide.src + slideIndex}
            className="monocle-slide-item"
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              animation: isVisible ? `${fadeKeyframeName} ${totalDuration}s infinite linear` : 'none',
              animationDelay: `${slideIndex * 4}s`,
              opacity: 0,
              pointerEvents: 'none',
            }}
          >
            <SmartCropImage
              src={slide.src}
              alt=""
              focalX={slide.focalX}
              focalY={slide.focalY}
              style={{ width: '100%', height: '100%' }}
            />
          </div>
        );
      })}
    </div>
  );
}
