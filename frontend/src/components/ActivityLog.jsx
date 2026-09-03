import React, { useState, useEffect, useRef } from 'react';
import { getActivityLog } from '../api';
import './ActivityLog.css';

function ActivityLog() {
  const [entries, setEntries] = useState([]);
  const lastIdRef = useRef(0);
  const pollingRef = useRef(false);

  useEffect(() => {
    let mounted = true;

    const poll = async () => {
      if (pollingRef.current) return;
      pollingRef.current = true;
      try {
        const data = await getActivityLog(lastIdRef.current);
        if (!mounted) return;
        // Detect backend restart: we asked for entries after id N but got nothing.
        // If since_id > 0 and response is empty, reset cursor to fetch from start.
        if ((!data.entries || data.entries.length === 0) && lastIdRef.current > 0) {
          lastIdRef.current = 0;
        }
        if (data.entries && data.entries.length > 0) {
          const maxId = Math.max(...data.entries.map(e => e.id));
          lastIdRef.current = maxId;
          setEntries(prev => {
            const newEntries = [...data.entries].reverse();
            const combined = [...newEntries, ...prev];
            const seen = new Set();
            return combined.filter(e => {
              if (seen.has(e.id)) return false;
              seen.add(e.id);
              return true;
            }).slice(0, 50);
          });
        }
      } catch {
        // silently ignore polling errors
      } finally {
        pollingRef.current = false;
      }
    };

    poll();
    const interval = setInterval(poll, 2000);
    return () => { mounted = false; clearInterval(interval); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const levelIcon = (level) => {
    switch (level) {
      case 'ok':    return '✓';
      case 'error': return '✗';
      case 'info':  return '›';
      default:      return '·';
    }
  };

  return (
    <div className="activity-log">
      <div className="activity-log-body">
        {entries.length === 0 && (
          <div className="activity-log-empty">
            Waiting for activity…<br />
            Click <strong>Generate</strong> or <strong>Refresh All</strong> to start.
          </div>
        )}
        {entries.map((entry) => (
          <div key={entry.id} className={`log-entry log-${entry.level}`}>
            <span className="log-ts">{entry.ts}</span>
            <span className={`log-level-icon level-${entry.level}`}>{levelIcon(entry.level)}</span>
            <span className="log-source">{entry.source}</span>
            <span className="log-msg">{entry.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default ActivityLog;
