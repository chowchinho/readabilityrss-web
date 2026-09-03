import { useState, useEffect, useRef, useCallback } from 'react';

const DEFAULT_DWELL_MS = 1000;
const DEFAULT_CLOSE_GRACE_MS = 180;

/**
 * Hook for managing hover-dwell panels that open after a delay
 * and remain open across a gap with a close-grace timeout.
 */
export default function useHoverDwellPanel({
  dwellMs = DEFAULT_DWELL_MS,
  closeGraceMs = DEFAULT_CLOSE_GRACE_MS,
} = {}) {
  const [showInfo, setShowInfo] = useState(false);
  const dwellTimerRef = useRef(null);
  const closeTimerRef = useRef(null);

  useEffect(() => () => {
    clearTimeout(dwellTimerRef.current);
    clearTimeout(closeTimerRef.current);
  }, []);

  const startDwell = useCallback(() => {
    clearTimeout(closeTimerRef.current);
    clearTimeout(dwellTimerRef.current);
    dwellTimerRef.current = setTimeout(() => setShowInfo(true), dwellMs);
  }, [dwellMs]);

  const stopDwell = useCallback(() => {
    clearTimeout(dwellTimerRef.current);
  }, []);

  const cancelDwellWithGrace = useCallback(() => {
    clearTimeout(dwellTimerRef.current);
    clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => setShowInfo(false), closeGraceMs);
  }, [closeGraceMs]);

  const keepPanelOpen = useCallback(() => {
    clearTimeout(closeTimerRef.current);
  }, []);

  const closePanelNow = useCallback(() => {
    clearTimeout(dwellTimerRef.current);
    clearTimeout(closeTimerRef.current);
    setShowInfo(false);
  }, []);

  const toggleInfo = useCallback((e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    clearTimeout(dwellTimerRef.current);
    setShowInfo((v) => !v);
  }, []);

  return {
    showInfo,
    setShowInfo,
    startDwell,
    stopDwell,
    cancelDwellWithGrace,
    keepPanelOpen,
    closePanelNow,
    toggleInfo,
  };
}
