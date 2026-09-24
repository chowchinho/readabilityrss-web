import { useCallback, useEffect, useState } from 'react';
import { getTranslationStatus } from '../api';

const WINDOWS = [
  { hours: 1, label: 'Last hour' },
  { hours: 6, label: 'Last 6 hours' },
  { hours: 12, label: 'Last 12 hours' },
  { hours: 24, label: 'Last 24 hours' },
  { hours: 72, label: 'Last 3 days' },
  { hours: 168, label: 'Last 7 days' },
];

const PROVIDER_COLORS = ['var(--accent, #c97d2e)', '#6b8f9c', '#8f7a9c', '#7a9c6b', '#9c6b6b'];
const REFRESH_MS = 30000;

// The API reports naive UTC; the dashboard is read in local time.
function utc(ts) {
  return ts ? new Date(ts.replace(' ', 'T') + 'Z') : null;
}

function formatWait(min) {
  if (min == null) return '—';
  if (min < 60) return `${Math.round(min)} min`;
  return `${(min / 60).toFixed(1)} h`;
}

function formatAgo(ts) {
  const d = utc(ts);
  if (!d) return '—';
  return formatWait((Date.now() - d.getTime()) / 60000) + ' ago';
}

function bucketLabel(start, bucketMinutes) {
  const d = utc(start);
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  if (bucketMinutes < 360) return hm;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export default function TranslationStatus() {
  const [hours, setHours] = useState(12);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    getTranslationStatus(hours)
      .then(d => { setData(d); setError(''); })
      .catch(e => setError(String(e.message || e)));
  }, [hours]);

  useEffect(() => {
    setData(null);
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const s = data && data.summary;
  const colorOf = {};
  (data ? data.providers : []).forEach((p, i) => {
    colorOf[p.provider] = PROVIDER_COLORS[i % PROVIDER_COLORS.length];
  });

  return (
    <div className="options-section">
      <div className="options-panel-head">
        <div>
          <div className="options-section-title" style={{ marginBottom: 4 }}>Translation Status</div>
          <div className="options-row-desc">
            Read back from the articles themselves. Waits run from arrival to translation.
            The queue refreshes every 30 seconds.
          </div>
        </div>
        <select
          className="options-input glossary-input"
          style={{ width: 'auto', flex: '0 0 auto' }}
          value={hours}
          onChange={e => setHours(Number(e.target.value))}
        >
          {WINDOWS.map(w => <option key={w.hours} value={w.hours}>{w.label}</option>)}
        </select>
      </div>

      {error && <div className="options-inline-error">{error}</div>}
      {!data && !error && <div className="options-row-desc">Loading…</div>}

      {data && (
        <>
          <div className="options-stat-row">
            <div className="options-stat">
              <div className="options-stat-value">{s.translated.toLocaleString()}</div>
              <div className="options-stat-label">Articles translated</div>
            </div>
            <div className="options-stat">
              <div className="options-stat-value">
                {s.success_rate == null ? '—' : `${(s.success_rate * 100).toFixed(1)}%`}
              </div>
              <div className="options-stat-label">
                Success rate · {s.fallbacks} via fallback · {s.failed} failed
                {s.unbadged > 0 && ` · ${s.unbadged} unbadged`}
              </div>
            </div>
            <div className="options-stat">
              <div className="options-stat-value">{data.queue.total}</div>
              <div className="options-stat-label">In queue now</div>
            </div>
            <div className="options-stat">
              <div className="options-stat-value">{data.queue.oldest ? formatAgo(data.queue.oldest) : '—'}</div>
              <div className="options-stat-label">Oldest queued</div>
            </div>
          </div>

          <div className="usage-section" style={{ marginTop: 20 }}>
            <div className="options-row-label">By provider</div>
            {data.providers.length === 0 ? (
              <div className="options-row-desc">Nothing translated in this window.</div>
            ) : (
              <table className="usage-table">
                <thead>
                  <tr>
                    <th>Provider</th><th className="num">Articles</th>
                    <th className="num">Avg wait</th><th className="num">Max wait</th>
                    <th className="num">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.providers.map(p => (
                    <tr key={p.provider}>
                      <td>
                        <span className="usage-swatch" style={{ background: colorOf[p.provider], marginLeft: 0, marginRight: 6 }} />
                        {p.provider}
                        {p.fallbacks > 0 && <span className="sub"> ({p.fallbacks} as fallback)</span>}
                      </td>
                      <td className="num">{p.articles.toLocaleString()}</td>
                      <td className="num">{formatWait(p.avg_wait_min)}</td>
                      <td className="num">{formatWait(p.max_wait_min)}</td>
                      <td className="num">{p.cost_cny > 0 ? `¥${p.cost_cny.toFixed(3)}` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="usage-section">
            <div className="usage-legend-row">
              <span className="options-row-label">Arrived vs translated <span className="tstatus-note">(local time)</span></span>
              <span className="usage-legend">
                {data.providers.map(p => (
                  <span key={p.provider}>
                    <span className="usage-swatch" style={{ background: colorOf[p.provider] }} /> {p.provider}
                  </span>
                ))}
                <span className="usage-swatch tstatus-arrived-swatch" /> arrived
              </span>
            </div>
            <div className="usage-bars">
              {(() => {
                const max = Math.max(
                  1,
                  ...data.series.map(b => Math.max(
                    b.arrived,
                    Object.values(b.translated).reduce((a, n) => a + n, 0),
                  )),
                );
                return data.series.map(b => {
                  const done = Object.values(b.translated).reduce((a, n) => a + n, 0);
                  return (
                    <div className="usage-bar-row tstatus-bar-row" key={b.start}>
                      <span className="usage-bar-day">{bucketLabel(b.start, data.bucket_minutes)}</span>
                      <span className="tstatus-bar-stack">
                        <span className="usage-bar-track">
                          {data.providers.map(p => b.translated[p.provider] ? (
                            <span
                              key={p.provider}
                              className="usage-bar-fill"
                              title={`${p.provider}: ${b.translated[p.provider]}`}
                              style={{
                                width: (b.translated[p.provider] / max) * 100 + '%',
                                background: colorOf[p.provider],
                                borderRadius: 0,
                              }}
                            />
                          ) : null)}
                        </span>
                        <span className="tstatus-arrived-track">
                          <span
                            className="tstatus-arrived-fill"
                            style={{ width: (b.arrived / max) * 100 + '%' }}
                          />
                        </span>
                      </span>
                      <span className="usage-bar-value">{b.arrived} in · {done} done</span>
                    </div>
                  );
                });
              })()}
            </div>
          </div>

          <div className="usage-section">
            <div className="options-row-label">Active queue</div>
            {data.queue.by_feed.length === 0 ? (
              <div className="options-row-desc">Queue is empty.</div>
            ) : (
              <table className="usage-table">
                <thead>
                  <tr>
                    <th>Feed</th><th>Translator</th>
                    <th className="num">Waiting</th><th className="num">Oldest</th>
                  </tr>
                </thead>
                <tbody>
                  {data.queue.by_feed.map((q, i) => (
                    <tr key={(q.source || '') + i}>
                      <td>{q.source || '(deleted feed)'}</td>
                      <td className="sub">{q.translator || '—'}</td>
                      <td className="num">{q.count}</td>
                      <td className="num">{formatAgo(q.oldest)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
