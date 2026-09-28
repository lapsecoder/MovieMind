import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { AboutPanel, StatusPill } from './components/AboutPanel'
import { MovieDetails } from './components/MovieDetails'
import { Recommendations } from './components/Recommendations'
import { SearchPanel } from './components/SearchPanel'
import {
  DEFAULT_K,
  SEARCH_DEBOUNCE_MS,
  type KOption,
} from './config'
import { useAbout, useReadiness } from './hooks/useAbout'
import { useDebouncedValue } from './hooks/useDebouncedValue'
import { useMovieDetails } from './hooks/useMovieDetails'
import { useMovieSearch } from './hooks/useMovieSearch'
import { useRecommendations } from './hooks/useRecommendations'
import type { RecommendationItem, SearchHit } from './api/types'

/**
 * The whole application.
 *
 * One piece of selection state, `selectedMovieId`, is the spine of the app:
 * search sets it, details reads it, and recommendations are keyed on it. Keeping
 * a single id rather than a whole film object is deliberate - the hooks fetch
 * the film themselves, so the id cannot go stale relative to what the API
 * returns, and there is exactly one source of truth for "what am I looking at".
 *
 * Recommendations load **automatically** on selection rather than behind a
 * button. The request is a single indexed lookup measured at ~1.02 ms, so making
 * the user click to see it only adds a step and a state that has to be explained.
 */
export function App(): ReactNode {
  const [query, setQuery] = useState('')
  const [selectedMovieId, setSelectedMovieId] = useState<number | null>(null)
  const [k, setK] = useState<KOption>(DEFAULT_K)

  const debouncedQuery = useDebouncedValue(query, SEARCH_DEBOUNCE_MS)
  const { state: searchState } = useMovieSearch(debouncedQuery)
  const detailsState = useMovieDetails(selectedMovieId)
  const recommendationState = useRecommendations(selectedMovieId, k)
  const about = useAbout()
  const readiness = useReadiness()

  // Keep the details' film title available for the document title, so a selected
  // film is identifiable from the browser tab and in history entries.
  const selectedTitle =
    detailsState.status === 'ready' ? detailsState.data.title : null

  useEffect(() => {
    document.title = selectedTitle
      ? `${selectedTitle} · MovieMind`
      : 'MovieMind'
  }, [selectedTitle])

  const onSelectFromSearch = useCallback((hit: SearchHit) => {
    setSelectedMovieId(hit.movie_id)
  }, [])

  /*
   * Choosing a recommendation makes it the new query film.
   *
   * This is the natural reading of clicking a result, and it turns the app into
   * a walk through the catalogue. It is a genuine state change - a new request
   * replaces the old list - so the selection is explicit rather than inferred.
   */
  const onSelectRecommendation = useCallback((movie: RecommendationItem) => {
    setSelectedMovieId(movie.movie_id)
  }, [])

  const onReset = useCallback(() => {
    setSelectedMovieId(null)
  }, [])

  const readinessLabel: Record<typeof readiness, { label: string; tone: 'ready' | 'loading' | 'error' }> = {
    checking: { label: 'Checking catalogue', tone: 'loading' },
    ready: { label: 'Catalogue ready', tone: 'ready' },
    not_ready: { label: 'Catalogue not loaded', tone: 'error' },
    unreachable: { label: 'API unreachable', tone: 'error' },
  }
  const status = readinessLabel[readiness]

  return (
    <div className="min-h-dvh bg-base text-ink">
      {/*
       * Skip link. The two-column layout puts a long results list before the
       * results panel in the DOM, so a keyboard user would otherwise tab through
       * every result on each page to reach the recommendations they just asked
       * for. This is the standard remedy and it is the first focusable element.
       */}
      <a
        href="#main"
        className="sr-only sr-only-focusable absolute left-3 top-3 z-50 rounded bg-accent px-3 py-2 text-sm font-semibold text-accent-ink"
      >
        Skip to main content
      </a>

      <header className="border-b border-line bg-surface/60">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-ink">
              MovieMind
            </h1>
            <p className="text-xs text-ink-faint">
              Films that read alike, found by text similarity.
            </p>
          </div>
          <StatusPill label={status.label} tone={status.tone} />
        </div>
      </header>

      <main
        id="main"
        className="mx-auto grid max-w-6xl grid-cols-1 gap-4 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]"
      >
        {/*
         * Source order is search, then details, then results - the order the
         * task is performed in. On a wide screen CSS places search and details in
         * the left column and results in the right, which is a visual
         * rearrangement only; the tab order still follows what the user is
         * doing, and neither column needs scrolling independently.
         */}
        <div className="flex flex-col gap-4">
          <section
            aria-label="Search"
            className="rounded-xl border border-line bg-surface p-4"
          >
            <SearchPanel
              query={query}
              onQueryChange={setQuery}
              state={searchState}
              selectedId={selectedMovieId}
              onSelect={onSelectFromSearch}
              onReset={onReset}
            />
          </section>

          <MovieDetails state={detailsState} />
        </div>

        <div className="flex flex-col gap-4">
          <Recommendations
            state={recommendationState}
            k={k}
            onKChange={setK}
            onSelectRecommendation={onSelectRecommendation}
            hasQuery={selectedMovieId !== null}
          />
          <AboutPanel about={about} />
        </div>
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-[11px] text-ink-faint sm:px-6">
        <p>
          Results are computed from indexed text, not from ratings or popularity.
          A similarity score is not a quality judgement.
        </p>
      </footer>
    </div>
  )
}
