import { useRef, type ReactNode } from 'react'

import { SEARCH_MIN_LENGTH } from '../config'
import type { SearchState } from '../hooks/useMovieSearch'
import { cx } from '../lib/format'
import {
  Chip,
  EmptyState,
  ErrorState,
  LiveStatus,
  SearchResultsSkeleton,
} from './primitives'
import type { MatchKind, SearchHit } from '../api/types'

/** Why each match tier outranks the ones below it. Shown as a tooltip. */
const MATCH_EXPLANATIONS: Record<MatchKind, string> = {
  exact: 'The title matches your search exactly.',
  title_prefix: 'The title starts with your search.',
  original_title_prefix: 'The original-language title starts with your search.',
  title_contains: 'The title contains your search somewhere.',
}

/**
 * The search column: input, status line, and results.
 *
 * **Keyboard model.** The results are a listbox (`role="listbox"`) whose options
 * are managed buttons, and the input is a combobox. That combination is what
 * makes ArrowDown/ArrowUp/Enter/Escape work without any custom focus juggling:
 *
 * - `ArrowDown` from the input focuses the first result; from a result, moves
 *   to the next.
 * - `Enter` on a result selects it.
 * - `Escape` clears the input.
 * - `Tab` moves out of the list as normal, because options are real buttons.
 *
 * A `<ul>` of `<button>`s would also be keyboard-operable, but `listbox` carries
 * the "this is a single-choice list, one of which is chosen" semantics that a
 * screen reader can announce, and it makes the active-descendant pattern
 * available. Buttons are kept rather than `role="option"` divs so that focus is
 * real focus and the browser's own activation behaviour still works.
 */
export function SearchPanel({
  query,
  onQueryChange,
  state,
  selectedId,
  onSelect,
  onReset,
}: {
  query: string
  onQueryChange: (next: string) => void
  state: SearchState
  selectedId: number | null
  onSelect: (movie: SearchHit) => void
  onReset: () => void
}): ReactNode {
  const inputRef = useRef<HTMLInputElement>(null)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const results = state.status === 'ready' || state.status === 'empty' ? state.data.results : []

  const focusOption = (index: number): void => {
    const clamped = Math.max(0, Math.min(index, results.length - 1))
    optionRefs.current[clamped]?.focus()
  }

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'ArrowDown' && results.length > 0) {
      event.preventDefault()
      focusOption(0)
    }
    if (event.key === 'Escape' && query.length > 0) {
      event.preventDefault()
      onQueryChange('')
      onReset()
    }
  }

  const onOptionKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ): void => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusOption(index + 1)
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (index === 0) {
        inputRef.current?.focus()
      } else {
        focusOption(index - 1)
      }
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label
          htmlFor="movie-search"
          className="block text-sm font-medium text-ink"
        >
          Find a film
        </label>
        <p id="movie-search-hint" className="mt-0.5 text-xs text-ink-faint">
          Search by title. Then pick one to see films like it.
        </p>
        <div className="relative mt-2">
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
            >
              <circle cx="7" cy="7" r="4.5" />
              <path d="M10.5 10.5 14 14" strokeLinecap="round" />
            </svg>
          </span>
          <input
            ref={inputRef}
            id="movie-search"
            type="search"
            role="combobox"
            aria-expanded={results.length > 0}
            aria-controls="movie-search-results"
            aria-describedby="movie-search-hint"
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            value={query}
            placeholder="e.g. Shawshank"
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={onInputKeyDown}
            className="w-full rounded-lg border border-line bg-surface-raised py-2.5 pl-9 pr-3 text-sm text-ink placeholder:text-ink-faint focus:border-line-strong focus:outline-none"
          />
        </div>
      </div>

      <SearchStatusLine state={state} query={query} />

      {state.status === 'loading' ? <SearchResultsSkeleton /> : null}

      {state.status === 'error' ? (
        <ErrorState
          title="Search failed"
          message={state.message}
          testId="search-error"
        />
      ) : null}

      {state.status === 'empty' ? (
        <EmptyState title={`No films match “${state.data.query}”.`} testId="search-empty">
          Try a shorter fragment of the title, or the original-language title.
        </EmptyState>
      ) : null}

      {state.status === 'ready' ? (
        <ul
          id="movie-search-results"
          role="listbox"
          aria-label="Search results"
          data-testid="search-results"
          className="space-y-1.5"
        >
          {results.map((hit, index) => (
            <li key={hit.movie_id} role="none">
              <button
                ref={(node) => {
                  optionRefs.current[index] = node
                }}
                type="button"
                role="option"
                aria-selected={hit.movie_id === selectedId}
                onClick={() => onSelect(hit)}
                onKeyDown={(event) => onOptionKeyDown(event, index)}
                className={cx(
                  'w-full rounded-lg border px-3 py-2.5 text-left transition-colors',
                  hit.movie_id === selectedId
                    ? 'border-accent bg-accent/10'
                    : 'border-line bg-surface hover:border-line-strong hover:bg-surface-hover',
                )}
              >
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm font-medium text-ink">
                    {hit.title ?? 'Untitled'}
                  </span>
                  <span className="flex shrink-0 items-baseline gap-2">
                    {/*
                     * The match tier is shown because the ordering is not a
                     * relevance score the user can infer. `exact` and the two
                     * prefix tiers outrank `title_contains` structurally, so
                     * saying which one applies explains why a result sits where
                     * it does instead of implying the list is scored.
                     */}
                    <span
                      className="rounded border border-line px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-faint"
                      title={MATCH_EXPLANATIONS[hit.match]}
                    >
                      {hit.match.replace(/_/g, ' ')}
                    </span>
                    {hit.release_year ? (
                      <span className="font-mono text-xs text-ink-faint">
                        {hit.release_year}
                      </span>
                    ) : null}
                  </span>
                </span>
                {hit.genres.length > 0 ? (
                  <span className="mt-1.5 flex flex-wrap gap-1.5">
                    {hit.genres.slice(0, 3).map((genre) => (
                      <Chip key={genre}>{genre}</Chip>
                    ))}
                    {hit.genres.length > 3 ? (
                      <span className="text-[11px] text-ink-faint">
                        +{hit.genres.length - 3}
                      </span>
                    ) : null}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/**
 * One line describing the search state, and the app's loading announcement.
 *
 * The counts come from the API's own `total_matches`, which is the number found
 * *before* `limit` applied. Showing "10 of 1,701" rather than "10 results" is
 * what stops a capped list from implying the catalogue holds ten films.
 */
function SearchStatusLine({
  state,
  query,
}: {
  state: SearchState
  query: string
}): ReactNode {
  let message = ''
  if (state.status === 'loading') message = 'Searching…'
  else if (state.status === 'ready') {
    const { count, total_matches } = state.data
    message =
      count < total_matches
        ? `Showing ${count} of ${total_matches.toLocaleString('en-GB')} matches.`
        : `${count} match${count === 1 ? '' : 'es'}.`
  } else if (state.status === 'empty') message = 'No matches.'
  else if (query.trim().length > 0 && query.trim().length < SEARCH_MIN_LENGTH) {
    message = `Type at least ${SEARCH_MIN_LENGTH} characters.`
  }

  return <LiveStatus message={message} />
}
