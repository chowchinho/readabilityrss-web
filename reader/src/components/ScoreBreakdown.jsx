import React, { useEffect, useState } from 'react';
import { getScoreBreakdown } from '../api';

const AXIS_NAMES = { topic: 'Topic', type: 'Type', region: 'Region', secondary: 'Also' };

/**
 * THE single rendering of the score breakdown. Every surface that explains why an
 * article is where it is renders this component and nothing else:
 *
 *   1. the index cards        - RankingControls' floating popover
 *   2. pane 2's cards         - the same RankingControls popover
 *   3. the reader's info panel - ArticleReader's .reader-info-panel
 *
 * They were three separate copies, which is how the reader panel ended up showing only
 * tags while the popover showed the full arithmetic. Change the breakdown here and all
 * three move together; do not reintroduce a local copy of this markup anywhere.
 *
 * CSS lives in ranking_controls.css and is deliberately unscoped, since this renders
 * inside a portal on document.body in one case and inside the reader in another.
 */
export function useScoreBreakdown(articleId, active) {
  const [breakdown, setBreakdown] = useState(null);
  const [error, setError] = useState(false);

  // A different article through the same component must not show the last one's sums.
  useEffect(() => {
    setBreakdown(null);
    setError(false);
  }, [articleId]);

  useEffect(() => {
    if (!active || !articleId || breakdown || error) return;
    let cancelled = false;
    getScoreBreakdown(articleId)
      .then((data) => { if (!cancelled) setBreakdown(data); })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [active, articleId, breakdown, error]);

  return { breakdown, breakdownError: error };
}

export default function ScoreBreakdown({ article, breakdown, breakdownError, showReason = false }) {
  const score = typeof article?.score === 'number' ? article.score.toFixed(2) : null;
  const terms = breakdown ? (breakdown.terms || breakdown.rows || []) : [];

  return (
    <>
      {showReason && (
        <div className="info-reason">
          <strong>Reason:</strong> {article?.recommendation_reason || 'Not yet tagged'}
        </div>
      )}

      <div className="info-score-head">
        <span>Score</span>
        <strong>
          {breakdown ? (breakdown.total ?? breakdown.score ?? 0).toFixed(2) : (score ?? '—')}
        </strong>
      </div>

      {breakdown ? (
        <table className="info-breakdown">
          <tbody>
            {terms.map((t, i) => (
              <tr key={i} className={`bd-row bd-${t.kind}`}>
                <td className="bd-label">
                  {t.kind === 'axis' ? `${AXIS_NAMES[t.axis] || t.axis}: ${t.label}` : t.label}
                  {t.kind === 'axis' && t.multiplier !== 1.0 && (
                    <span className="bd-mult"> x{t.multiplier}</span>
                  )}
                  {t.kind === 'secondary_subtotal' && (
                    <span className="bd-mult">
                      {` subtotal ${t.raw.toFixed(2)}${t.clamped ? ', clamped' : ''}, x${t.multiplier}`}
                    </span>
                  )}
                </td>
                {/* Most secondary labels have no declared weight and too few votes of
                    their own, so they inherit a baseline from the topics they usually
                    appear under. Shown in place of "declared" because that is the role
                    it plays - without it the value column would not reconcile. */}
                <td
                  className="bd-declared"
                  title={t.kind === 'axis' && t.prior
                    ? 'Inherited from the topics this label usually appears under, fading as it earns its own votes'
                    : undefined}
                >
                  {t.kind !== 'axis' ? '' : (
                    Math.abs(t.prior || 0) > 0.005 && !t.declared
                      ? `inherited ${t.prior > 0 ? '+' : ''}${t.prior.toFixed(2)}`
                      : `declared ${t.declared.toFixed(1)}`
                  )}
                </td>
                <td className="bd-votes">
                  {t.kind === 'axis' ? `${t.votes} votes` : ''}
                </td>
                {/* The two channels are capped separately (votes +/-4, reading habits
                    +/-1), so which one moved a label has to be legible. */}
                <td className="bd-behav">
                  {t.kind === 'axis' && t.behavioural
                    ? `${t.behavioural > 0 ? '+' : ''}${t.behavioural.toFixed(2)} read`
                    : ''}
                </td>
                {/* Secondary rows are shown for provenance but are not summed here - the
                    subtotal row applies the axis total. Parenthesised so the unbracketed
                    column adds up to Total. */}
                <td className={`bd-value${t.kind === 'axis' && t.axis === 'secondary' ? ' is-info' : ''}`}>
                  {t.kind === 'axis' && t.axis === 'secondary'
                    ? `(${t.raw.toFixed(2)})`
                    : t.contribution.toFixed(2)}
                </td>
              </tr>
            ))}
            <tr className="bd-total">
              <td colSpan={4}>Total</td>
              <td className="bd-value">
                {(breakdown.total ?? breakdown.score ?? 0).toFixed(2)}
              </td>
            </tr>
          </tbody>
        </table>
      ) : (
        <div className="info-tags">
          <span>Topic: {article?.topics?.primary || 'unknown'}</span>{' · '}
          <span>Region: {article?.topics?.region || 'unknown'}</span>{' · '}
          <span>Type: {article?.topics?.type || 'unknown'}</span>
          {article?.topics?.secondary?.length > 0 && (
            <div className="info-tags">Also: {article.topics.secondary.join(', ')}</div>
          )}
          {breakdownError && <div className="info-offline">Breakdown unavailable offline</div>}
        </div>
      )}

      {article?.ai_summary && (
        <div className="info-summary"><strong>Summary:</strong> {article.ai_summary}</div>
      )}
    </>
  );
}
