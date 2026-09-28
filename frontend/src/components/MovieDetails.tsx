import type { ReactNode } from 'react'

import type { DetailsState } from '../hooks/useMovieDetails'
import {
  Chip,
  DetailsSkeleton,
  EmptyState,
  ErrorState,
  Panel,
  Stat,
} from './primitives'

/**
 * The selected film.
 *
 * **There is no overview here, and that is intentional rather than an omission.**
 * TMDb synopsis prose exists in the gitignored raw snapshot but never reaches
 * the processed corpus: `data/processed/movies.jsonl` stores the tokenised
 * `document`/`tokens` that Phase 3 derives, and Phase 4 pins that file's SHA-256
 * so it cannot quietly change. `/api/v1/movies/{id}` therefore has no overview
 * field to render, and the client cannot obtain one without either inventing
 * plot text or calling TMDb from the browser - which would put an API key in
 * front-end source. Both are worse than an acknowledged gap, so the panel shows
 * the metadata that genuinely exists and names the omission once.
 *
 * `feature_counts` is shown instead of a synopsis because it is the honest
 * answer to "what is this app actually comparing here": it is the breakdown of
 * the indexed text the similarity score is computed from.
 */
export function MovieDetails({ state }: { state: DetailsState }): ReactNode {
  if (state.status === 'idle') {
    return (
      <Panel title="Selected film" id="details">
        <EmptyState title="No film selected yet." testId="details-idle">
          Search above and choose a title to see its details.
        </EmptyState>
      </Panel>
    )
  }

  if (state.status === 'loading') {
    return (
      <Panel title="Selected film" id="details">
        <DetailsSkeleton />
      </Panel>
    )
  }

  if (state.status === 'error') {
    return (
      <Panel title="Selected film" id="details">
        <ErrorState
          title="Could not load this film"
          message={state.message}
          testId="details-error"
        />
      </Panel>
    )
  }

  const movie = state.data
  const featureEntries = Object.entries(movie.feature_counts ?? {})

  return (
    <Panel title="Selected film" id="details">
      <article data-testid="details-panel">
        <header>
          <h3 className="text-lg font-semibold leading-tight text-ink">
            {movie.title ?? 'Untitled'}
          </h3>
          <p className="mt-1 font-mono text-xs text-ink-faint">
            {movie.release_year ?? 'Year unknown'}
            {movie.original_language ? ` · ${movie.original_language}` : ''}
            {movie.adult ? ' · adult' : ''}
          </p>
          {movie.original_title && movie.original_title !== movie.title ? (
            <p className="mt-0.5 text-xs text-ink-faint">
              Original title: {movie.original_title}
            </p>
          ) : null}
        </header>

        {movie.genres.length > 0 ? (
          <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Genres">
            {movie.genres.map((genre) => (
              <li key={genre}>
                <Chip>{genre}</Chip>
              </li>
            ))}
          </ul>
        ) : null}

        {movie.collection_name ? (
          <p className="mt-2 text-xs text-ink-muted">
            Part of {movie.collection_name}
          </p>
        ) : null}

        <p className="mt-3 text-xs text-ink-faint" data-testid="no-overview-note">
          No plot summary: synopsis text stays in the private raw snapshot and is
          never sent to the browser.
        </p>

        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-4 sm:grid-cols-4">
          <Stat
            label="Runtime"
            value={formatRuntime(movie.runtime_minutes)}
            hint="0 means TMDb recorded no runtime, not a zero-length film."
          />
          <Stat
            label="Released"
            value={movie.release_date ?? '—'}
          />
          <Stat
            label="Vote"
            value={formatVote(movie.vote_average, movie.vote_count)}
            hint="TMDb's own 0-10 audience average. This is not a MovieMind score."
          />
          <Stat
            label="Indexed terms"
            value={movie.document_size ?? '—'}
            hint="Terms in this film's TF-IDF vector. More text means more to match on."
          />
        </dl>

        {!movie.recommendable ? (
          <p
            className="mt-4 rounded-lg border border-line bg-surface-raised px-3 py-2 text-xs text-ink-muted"
            data-testid="not-recommendable"
          >
            This film has too little indexed text to be used as a query, so
            recommendations cannot be computed for it. It can still be a
            <em> result</em> for other films.
          </p>
        ) : null}

        {featureEntries.length > 0 ? (
          <details className="mt-4 border-t border-line pt-3">
            <summary className="cursor-pointer text-xs font-medium text-ink-muted hover:text-ink">
              What &ldquo;like this&rdquo; actually compares
            </summary>
            <p className="mt-2 text-xs text-ink-faint">
              How many times each signal appears in this film&rsquo;s indexed
              text. These counts are the raw material for the similarity score -
              a film with more of them has more to match against.
            </p>
            <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
              {featureEntries.map(([field, count]) => (
                <li
                  key={field}
                  className="flex items-baseline justify-between gap-2 text-xs"
                >
                  <span className="truncate text-ink-muted">
                    {field.replace(/_/g, ' ')}
                  </span>
                  <span className="font-mono text-ink-faint">{count}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </article>
    </Panel>
  )
}

/**
 * Runtime as `h m`, or nothing.
 *
 * Returns `—` for TMDb's `0` sentinel, which means "not recorded". A film with a
 * genuine zero-length runtime does not exist, so rendering `0m` would assert
 * something false about the data.
 */
function formatRuntime(minutes: number | null): string {
  if (minutes === null || minutes <= 0) return '—'
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours === 0) return `${rest}m`
  if (rest === 0) return `${hours}h`
  return `${hours}h ${rest}m`
}

/**
 * TMDb vote average with its count, or nothing.
 *
 * `0.0` with zero votes is the unrated sentinel. Printed alone it would read as
 * "audiences rated this zero" - a different and wrong claim - so the count has
 * to travel with it to make the distinction visible.
 */
function formatVote(average: number | null, count: number | null): string {
  if (average === null || count === null || count === 0) return '—'
  return `${average.toFixed(1)} / 10 (${count.toLocaleString('en-GB')} votes)`
}
