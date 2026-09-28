import { useMemo, type ReactNode } from 'react'

import { K_OPTIONS, type KOption } from '../config'
import type { RecommendationsState } from '../hooks/useRecommendations'
import type {
  RecommendationItem,
  RecommendationResponse,
  SkipReason,
} from '../api/types'
import { cx } from '../lib/format'
import {
  Chip,
  EmptyState,
  ErrorState,
  Panel,
  RankBadge,
  RecommendationSkeleton,
  ScoreReadout,
} from './primitives'

/**
 * Films like the selected one, presented in the engine's own order.
 *
 * Three decisions worth stating, because each is a place a front end could
 * quietly reimplement the backend or overstate what the numbers mean:
 *
 * 1. **The order is never touched.** `rank` arrives from the engine and the
 *    list renders in array order, which is the order the engine produced. The
 *    client does not sort by `score`, re-rank, or re-filter, because a second
 *    ordering rule in TypeScript is a second set of answers to the same
 *    question, and it would drift the moment the engine's tie-breaking changed.
 * 2. **`score` is shown raw and labelled.** It is a cosine similarity between
 *    TF-IDF vectors. Phase 4 sec 4 measured a median top-1 cosine of 0.281,
 *    with 64.2% of queries under 0.30 - so "28% match" would read as a
 *    probability or a quality grade, neither of which it is. No bar, no
 *    percentage, no stars. `shared_terms` sits beside it because Phase 4 sec 4
 *    also shows evidence *count* is what separates a 1-term match from a
 *    30-term one when magnitude cannot.
 * 3. **The skipped list is tallied, never listed.** One measured query returned
 *    1,837 skipped candidates - 160 KB of JSON. Rendering that would cost the
 *    user a long scroll to convey a fact already in `evidence`. The per-reason
 *    counts are derived from the array the engine already sent, and labelled as
 *    derived so nobody mistakes the tally for a field the API provided.
 */
export function Recommendations({
  state,
  k,
  onKChange,
  onSelectRecommendation,
  hasQuery,
}: {
  state: RecommendationsState
  k: KOption
  onKChange: (next: KOption) => void
  onSelectRecommendation: (movie: RecommendationItem) => void
  hasQuery: boolean
}): ReactNode {
  return (
    <Panel
      title="Films like this"
      id="recommendations"
      action={
        <div
          role="group"
          aria-label="Number of recommendations"
          className="flex items-center gap-1"
        >
          {K_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onKChange(option)}
              aria-pressed={option === k}
              className={cx(
                // min-w/min-h keep every option (including the single-digit "5")
                // at or above the 24x24 pointer target from WCAG 2.5.8.
                'min-h-6 min-w-6 rounded px-2 py-1 font-mono text-xs transition-colors',
                option === k
                  ? 'bg-accent text-accent-ink'
                  : 'text-ink-muted hover:bg-surface-hover hover:text-ink',
              )}
            >
              {option}
            </button>
          ))}
        </div>
      }
    >
      {!hasQuery ? (
        <EmptyState title="Pick a film first." testId="recommendations-idle">
          Recommendations are computed against a chosen film, so choose one from
          the search results.
        </EmptyState>
      ) : state.status === 'idle' ? (
        <EmptyState title="Ready when you are." testId="recommendations-idle">
          Use <strong>Show films like this</strong> on the selected film to run
          the search.
        </EmptyState>
      ) : state.status === 'loading' ? (
        <RecommendationSkeleton rows={k > 10 ? 6 : 4} />
      ) : state.status === 'error' ? (
        <ErrorState
          title="Could not load recommendations"
          message={state.message}
          testId="recommendations-error"
        />
      ) : state.data.recommendations.length === 0 ? (
        <EmptyResults data={state.data} />
      ) : (
        <Results data={state.data} onSelect={onSelectRecommendation} />
      )}
    </Panel>
  )
}

/** The normal case: a ranked list plus the evidence behind it. */
function Results({
  data,
  onSelect,
}: {
  data: RecommendationResponse
  onSelect: (movie: RecommendationItem) => void
}): ReactNode {
  return (
    <div>
      <ol className="space-y-1.5" data-testid="recommendations-list">
        {data.recommendations.map((movie, index) => (
          <li key={movie.movie_id}>
            <button
              type="button"
              onClick={() => onSelect(movie)}
              data-testid="recommendation-item"
              className="flex w-full gap-3 rounded-lg border border-line bg-surface-raised/60 px-3 py-2.5 text-left transition-colors hover:border-line-strong hover:bg-surface-hover"
            >
              <RankBadge rank={movie.rank ?? index + 1} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-ink">
                  {movie.title ?? 'Untitled'}
                </span>
                <span className="mt-0.5 block font-mono text-xs text-ink-faint">
                  {movie.year ?? 'year unknown'}
                </span>
                {movie.genres.length > 0 ? (
                  <span className="mt-1.5 flex flex-wrap gap-1.5">
                    {movie.genres.slice(0, 3).map((genre) => (
                      <Chip key={genre}>{genre}</Chip>
                    ))}
                  </span>
                ) : null}
                <ScoreReadout
                  score={movie.score}
                  sharedTerms={movie.shared_terms}
                  minSharedTerms={data.evidence.min_shared_terms}
                />
              </span>
            </button>
          </li>
        ))}
      </ol>
      <EvidenceSummary data={data} />
    </div>
  )
}

/**
 * A result set that is legitimately empty.
 *
 * This is information, not failure. The engine examined a set of candidates and
 * every one was rejected - most often for sharing too few indexed terms - and
 * saying "no results" alone would read as a broken search. The two distinct
 * causes are separated, because they are genuinely different: `candidates_examined === 0`
 * means nothing was comparable at all, while a large rejected count means plenty
 * was comparable but nothing cleared the evidence threshold.
 */
function EmptyResults({ data }: { data: RecommendationResponse }): ReactNode {
  const { evidence } = data
  const nothingExamined = evidence.candidates_examined === 0

  return (
    <div data-testid="recommendations-empty">
      <EmptyState
        title={
          nothingExamined
            ? 'No comparable film was found for this title.'
            : 'Nothing in the catalogue cleared the evidence threshold.'
        }
      >
        {nothingExamined ? (
          <p>
            Every other film shares too few indexed terms with it, so there is no
            meaningful match to show.
          </p>
        ) : (
          <p>
            {evidence.candidates_examined.toLocaleString('en-GB')} candidate
            {evidence.candidates_examined === 1 ? ' was' : 's were'} examined and
            all {evidence.candidates_rejected.toLocaleString('en-GB')} were
            rejected. A match needs at least {evidence.min_shared_terms} shared
            indexed term{evidence.min_shared_terms === 1 ? '' : 's'}.
          </p>
        )}
      </EmptyState>
      <EvidenceSummary data={data} />
    </div>
  )
}

/**
 * The engine's audit trail, always visible under the results.
 *
 * `returned / examined` is the honest framing of a short list: five results out
 * of 1,842 candidates is a specific fact, and hiding the denominator would make
 * a thin result look like a complete one.
 */
function EvidenceSummary({ data }: { data: RecommendationResponse }): ReactNode {
  const { evidence } = data

  // Derived from the skipped array, not an API field. Counting is safe: it is a
  // tally of what the engine sent, with no judgement applied. Memoised so it
  // does not recompute on every re-render of a 1,837-element array.
  const reasonCounts = useMemo(() => {
    const counts = new Map<SkipReason, number>()
    for (const entry of data.skipped) {
      counts.set(entry.reason, (counts.get(entry.reason) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [data.skipped])

  const derivedTotal = reasonCounts.reduce((sum, [, count]) => sum + count, 0)

  return (
    <div
      className="mt-4 border-t border-line pt-3 text-xs text-ink-faint"
      data-testid="evidence-summary"
    >
      <p>
        {evidence.returned} of {evidence.candidates_examined.toLocaleString('en-GB')}{' '}
        candidate{evidence.candidates_examined === 1 ? '' : 's'} examined met the
        evidence threshold of {evidence.min_shared_terms} shared indexed term
        {evidence.min_shared_terms === 1 ? '' : 's'};{' '}
        {evidence.candidates_rejected.toLocaleString('en-GB')} rejected.
        {evidence.exhausted ? ' The engine ran out of candidates to consider.' : ''}
      </p>
      {reasonCounts.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer hover:text-ink-muted">
            Why candidates were rejected
          </summary>
          <ul className="mt-1.5 space-y-0.5">
            {reasonCounts.map(([reason, count]) => (
              <li key={reason} className="flex justify-between gap-3">
                <span>{humaniseReason(reason)}</span>
                <span className="font-mono">{count.toLocaleString('en-GB')}</span>
              </li>
            ))}
          </ul>
          {derivedTotal !== evidence.candidates_rejected ? (
            <p className="mt-1.5 text-[11px] text-ink-faint">
              Counts cover the {derivedTotal.toLocaleString('en-GB')} skipped
              candidate{derivedTotal === 1 ? '' : 's'} listed by the engine.
            </p>
          ) : null}
        </details>
      ) : null}
    </div>
  )
}

/**
 * Turn a stable backend reason code into plain English.
 *
 * Matched exhaustively against `SkipReason`, with the raw code as the fallback so
 * an unrecognised reason is shown rather than dropped. Inventing a description
 * for an unknown code would be describing behaviour the engine does not document.
 */
function humaniseReason(reason: SkipReason): string {
  switch (reason) {
    case 'insufficient_shared_terms':
      return 'Too few shared indexed terms'
    case 'below_min_score':
      return 'Similarity below the threshold'
    default:
      return String(reason).replace(/_/g, ' ')
  }
}
