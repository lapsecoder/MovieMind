import type { ReactNode } from 'react'

import { cx, formatGenre } from '../lib/format'

/**
 * Shared presentational primitives.
 *
 * These exist because the same six shapes recur across search, details, and
 * recommendations: a block of placeholder bars, an empty message, an error
 * message, a labelled statistic, a genre chip, and a score readout. Collecting
 * them means the loading state looks the same everywhere, which is most of what
 * makes a UI feel considered.
 */

/* -------------------------------------------------------------------------- */
/* Skeletons                                                                   */
/* -------------------------------------------------------------------------- */


export function SkeletonBar({
  className,
}: {
  className?: string
}): ReactNode {
  return (
    <div
      className={cx('mm-skeleton rounded', className ?? 'h-3 w-full')}
      aria-hidden="true"
    />
  )
}

/**
 * Placeholder for the search results list.
 *
 * `aria-hidden` because the loading *state* is announced separately by a live
 * region in `SearchResults`; a screen reader reading eight anonymous grey bars
 * would be noise. The shape still matches the real rows so the layout does not
 * jump when results arrive.
 */
export function SearchResultsSkeleton({ rows = 5 }: { rows?: number }): ReactNode {
  return (
    <div className="space-y-2" data-testid="search-skeleton">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="rounded-lg border border-line bg-surface p-3"
        >
          <SkeletonBar className="h-3.5 w-2/3" />
          <div className="mt-2 flex gap-2">
            <SkeletonBar className="h-2.5 w-16" />
            <SkeletonBar className="h-2.5 w-12" />
          </div>
        </div>
      ))}
    </div>
  )
}

/** Placeholder for the details panel. Mirrors the real two-column layout. */
export function DetailsSkeleton(): ReactNode {
  return (
    <div className="space-y-4" data-testid="details-skeleton">
      <div>
        <SkeletonBar className="h-6 w-3/4" />
        <SkeletonBar className="mt-2 h-3 w-1/3" />
      </div>
      <div className="flex flex-wrap gap-2">
        <SkeletonBar className="h-5 w-16 rounded-full" />
        <SkeletonBar className="h-5 w-20 rounded-full" />
      </div>
      <div className="space-y-2 pt-2">
        <SkeletonBar className="h-2.5 w-full" />
        <SkeletonBar className="h-2.5 w-full" />
        <SkeletonBar className="h-2.5 w-2/3" />
      </div>
      <div className="grid grid-cols-2 gap-3 border-t border-line pt-4 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index}>
            <SkeletonBar className="h-2.5 w-14" />
            <SkeletonBar className="mt-1.5 h-3.5 w-20" />
          </div>
        ))}
      </div>
    </div>
  )
}

/** Placeholder rows for the recommendation list. */
export function RecommendationSkeleton({
  rows = 4,
}: {
  rows?: number
}): ReactNode {
  return (
    <ol className="space-y-2" data-testid="recommendations-skeleton">
      {Array.from({ length: rows }, (_, index) => (
        <li
          key={index}
          className="flex gap-3 rounded-lg border border-line bg-surface p-3"
        >
          <SkeletonBar className="h-7 w-7 shrink-0 rounded-md" />
          <div className="min-w-0 flex-1 space-y-2">
            <SkeletonBar className="h-3.5 w-1/2" />
            <SkeletonBar className="h-2.5 w-1/3" />
          </div>
        </li>
      ))}
    </ol>
  )
}

/* -------------------------------------------------------------------------- */
/* Messages                                                                    */
/* -------------------------------------------------------------------------- */

export function EmptyState({
  title,
  children,
  testId,
}: {
  title: string
  children?: ReactNode
  testId?: string
}): ReactNode {
  return (
    <div
      className="rounded-lg border border-dashed border-line px-4 py-8 text-center"
      data-testid={testId}
    >
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {children ? (
        <div className="mt-1.5 text-sm text-ink-faint">{children}</div>
      ) : null}
    </div>
  )
}

/**
 * A failure the user can read.
 *
 * `role="alert"` so it is announced the moment it appears - a request that fails
 * after the user has moved on would otherwise fail silently. The message is the
 * backend's own wording where one exists, because it is more specific than
 * anything this app could invent (e.g. it names the missing corpus file).
 */
export function ErrorState({
  title = 'Something went wrong',
  message,
  action,
  testId = 'error-state',
}: {
  title?: string
  message: string
  action?: ReactNode
  testId?: string
}): ReactNode {
  return (
    <div
      role="alert"
      data-testid={testId}
      className="rounded-lg border border-danger/40 bg-danger-surface/40 px-4 py-4"
    >
      <p className="text-sm font-semibold text-danger">{title}</p>
      <p className="mt-1 text-sm text-ink-muted">{message}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Small pieces                                                                */
/* -------------------------------------------------------------------------- */

/**
 * A genre, or any short categorical label.
 *
 * Genres arrive from the API lowercase and underscored (`science_fiction`)
 * because they are derived from the Phase 3 token namespace
 * (`gn:science_fiction` -> `science fiction`). Underscores are converted to
 * spaces and the result is title-cased for display only; the value rendered is
 * derived, never invented.
 */
export function Chip({ children }: { children: string }): ReactNode {
  return (
    <span className="inline-flex items-center rounded-full border border-line bg-surface-raised px-2.5 py-0.5 text-xs text-ink-muted">
      {formatGenre(children)}
    </span>
  )
}


/**
 * One labelled figure in the details stat row.
 *
 * Renders nothing when the value is absent. A `—` placeholder for a missing
 * runtime would be a claim that the runtime is unknown-but-known, and Phase 2
 * sec 9 found `runtime: 0` is TMDb's sentinel for *not recorded* - so showing a
 * number there would be inventing data. `feature_counts` is a different case:
 * a zero there is meaningful (the field was indexed and contributed nothing),
 * so those render as `0`.
 */
export function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: ReactNode
  hint?: string
}): ReactNode {
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-ink" title={hint}>
        {value}
      </dd>
    </div>
  )
}

/**
 * A similarity score.
 *
 * **Deliberately not a percentage.** The value is a cosine similarity between
 * TF-IDF vectors, and Phase 4 sec 4 established it is not calibrated: top-1
 * cosines have a median of 0.281 and 64.2% of queries fall below 0.30, so
 * "28%" would read as "this film is a 28% good match" and imply a probability
 * or a quality grade that the number does not carry. It is shown as the raw
 * cosine to two decimals, labelled as a score, and the evidence count
 * (`shared_terms`) sits beside it because Phase 4 sec 4 shows evidence *count*
 * separates a 1-term match from a 30-term one when magnitude cannot.
 */
export function ScoreReadout({
  score,
  sharedTerms,
  minSharedTerms,
}: {
  score: number
  sharedTerms: number
  minSharedTerms: number
}): ReactNode {
  const atThreshold = sharedTerms <= minSharedTerms
  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className="font-mono text-ink-muted"
        title="Cosine similarity between TF-IDF vectors. Not a percentage, and not a quality grade."
      >
        Similarity {score.toFixed(2)}
      </span>
      <span aria-hidden="true" className="text-ink-faint">
        &middot;
      </span>
      <span
        className={cx(
          atThreshold ? 'text-ink-faint' : 'text-ink-muted',
        )}
        title={`${sharedTerms} indexed terms shared with the query. The engine requires at least ${minSharedTerms}.`}
      >
        {sharedTerms} shared term{sharedTerms === 1 ? '' : 's'}
      </span>
    </div>
  )
}

/** Rank badge. Text plus position, so rank is never conveyed by position alone. */
export function RankBadge({ rank }: { rank: number }): ReactNode {
  return (
    <span
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line-strong bg-surface-raised font-mono text-xs font-semibold text-ink-muted"
      aria-label={`Rank ${rank}`}
    >
      {rank}
    </span>
  )
}

/** A titled region. `headingLevel` keeps the document outline correct. */
export function Panel({
  title,
  headingLevel = 2,
  action,
  children,
  className,
  id,
}: {
  title: string
  headingLevel?: 2 | 3
  action?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}): ReactNode {
  const Heading = headingLevel === 2 ? 'h2' : 'h3'
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-heading` : undefined}
      className={cx(
        'rounded-xl border border-line bg-surface',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <Heading
          id={id ? `${id}-heading` : undefined}
          className="text-sm font-semibold tracking-wide text-ink"
        >
          {title}
        </Heading>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}

/**
 * A live region for status messages.
 *
 * `aria-live="polite"` with `role="status"`: search results, counts, and
 * loading transitions are announced without interrupting whatever the user is
 * reading. Errors use `role="alert"` instead (`ErrorState`), which is
 * assertive, because a failure is the one thing that should interrupt.
 */
export function LiveStatus({ message }: { message: string }): ReactNode {
  return (
    <p
      role="status"
      aria-live="polite"
      data-testid="live-status"
      className="min-h-[1.25rem] text-xs text-ink-faint"
    >
      {message}
    </p>
  )
}
