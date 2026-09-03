import { useEffect, useRef } from 'react';

export function useSwipe({ onSwipeLeft, onSwipeRight, threshold = 50, maxTime = 300, enabled = true }) {
  const touchStartRef = useRef({ x: null, y: null, time: null });

  useEffect(() => {
    if (!enabled) return;

    const handleTouchStart = (e) => {
      touchStartRef.current = {
        x: e.touches[0].clientX,
        y: e.touches[0].clientY,
        time: Date.now()
      };
    };

    const handleTouchEnd = (e) => {
      if (!touchStartRef.current.x) return;

      const touchEndX = e.changedTouches[0].clientX;
      const touchEndY = e.changedTouches[0].clientY;
      const timeDiff = Date.now() - touchStartRef.current.time;

      const diffX = touchStartRef.current.x - touchEndX;
      const diffY = touchStartRef.current.y - touchEndY;

      // Ensure it's a horizontal swipe, not vertical scrolling
      if (timeDiff <= maxTime && Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > threshold) {
        if (diffX > 0 && onSwipeLeft) {
          onSwipeLeft(); // Swiped left -> Next article
        } else if (diffX < 0 && onSwipeRight) {
          onSwipeRight(); // Swiped right -> Previous article or Back
        }
      }

      touchStartRef.current = { x: null, y: null, time: null };
    };

    document.addEventListener('touchstart', handleTouchStart);
    document.addEventListener('touchend', handleTouchEnd);

    return () => {
      document.removeEventListener('touchstart', handleTouchStart);
      document.removeEventListener('touchend', handleTouchEnd);
    };
  }, [onSwipeLeft, onSwipeRight, threshold, maxTime, enabled]);
}
