import { useState, useEffect, useRef, useCallback } from 'react';
import { API_URL } from '../api';

const PROBE_SLOW_MS = 2 * 60 * 1000;
const PROBE_FAST_MS = 30 * 1000;
const PROBE_FAST_REQUIRED = 4;
const SYNC_FAILURE_THRESHOLD = 2;
const HEALTH_TIMEOUT_MS = 5000;

export function useOffline(enabled = true) {
  const [browserOffline, setBrowserOffline] = useState(!navigator.onLine);
  const [autoOffline, setAutoOffline] = useState(false);
  const [forcedOffline, setForcedOffline] = useState(false);

  const strikeCountRef = useRef(0);
  const probePhaseRef = useRef('idle');
  const probeSuccessCountRef = useRef(0);
  const probeTimerRef = useRef(null);

  const isOffline = enabled && (browserOffline || autoOffline || forcedOffline);

  useEffect(() => {
    if (!enabled) {
      setBrowserOffline(false);
      return undefined;
    }

    const handleOnline = () => setBrowserOffline(false);
    const handleOffline = () => setBrowserOffline(true);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, [enabled]);

  const clearProbeTimer = useCallback(() => {
    if (probeTimerRef.current) {
      clearInterval(probeTimerRef.current);
      probeTimerRef.current = null;
    }
  }, []);

  const stopProbing = useCallback(() => {
    clearProbeTimer();
    probePhaseRef.current = 'idle';
    probeSuccessCountRef.current = 0;
  }, [clearProbeTimer]);

  const releaseOffline = useCallback(() => {
    stopProbing();
    setAutoOffline(false);
    setForcedOffline(false);
    strikeCountRef.current = 0;
  }, [stopProbing]);

  const switchToProbePhase = useCallback((phase, runProbe) => {
    clearProbeTimer();
    probePhaseRef.current = phase;
    probeSuccessCountRef.current = 0;
    probeTimerRef.current = setInterval(
      runProbe,
      phase === 'slow' ? PROBE_SLOW_MS : PROBE_FAST_MS
    );
  }, [clearProbeTimer]);

  const startProbing = useCallback(() => {
    if (probePhaseRef.current !== 'idle') return;

    const runProbe = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
        const resp = await fetch(`${API_URL}/health`, {
          cache: 'no-store',
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (resp.ok) {
          if (probePhaseRef.current === 'slow') {
            switchToProbePhase('fast', runProbe);
          } else if (probePhaseRef.current === 'fast') {
            probeSuccessCountRef.current += 1;
            if (probeSuccessCountRef.current >= PROBE_FAST_REQUIRED) {
              releaseOffline();
            }
          }
          return;
        }
      } catch {
        // Fall through to reset logic below.
      }

      if (probePhaseRef.current === 'fast') {
        switchToProbePhase('slow', runProbe);
      }
    };

    switchToProbePhase('slow', runProbe);
  }, [releaseOffline, switchToProbePhase]);

  useEffect(() => {
    if (!enabled) {
      stopProbing();
      setAutoOffline(false);
      setForcedOffline(false);
      strikeCountRef.current = 0;
      return undefined;
    }

    if (isOffline) {
      startProbing();
    } else {
      stopProbing();
    }

    return () => {
      stopProbing();
    };
  }, [enabled, isOffline, startProbing, stopProbing]);

  const reportSyncFailure = useCallback(() => {
    if (!enabled) return;
    strikeCountRef.current += 1;
    if (strikeCountRef.current >= SYNC_FAILURE_THRESHOLD) {
      setAutoOffline(true);
    }
  }, [enabled]);

  const reportSyncSuccess = useCallback(() => {
    if (!enabled) return;
    strikeCountRef.current = 0;
    setAutoOffline(false);
  }, [enabled]);

  const toggleForcedOffline = useCallback(() => {
    if (!enabled) return;
    if (forcedOffline || autoOffline) {
      releaseOffline();
    } else {
      stopProbing();
      setForcedOffline(true);
    }
  }, [enabled, forcedOffline, autoOffline, releaseOffline, stopProbing]);

  return {
    isOffline,
    isForcedOffline: enabled && (forcedOffline || autoOffline),
    toggleForcedOffline,
    reportSyncFailure,
    reportSyncSuccess,
  };
}
