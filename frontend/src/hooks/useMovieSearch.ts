import { useEffect, useRef, useState } from 'react'

import { searchMovies } from '../api/client'
import { errorCode, isAbortError, isMovieMindError } from '../api/errors'
import { SEARCH_MIN_LENGTH } from '../config'
import type { SearchResponse } from '../api/types'

/**
 * Result state for a search. One discriminated union rather than three
 * independent booleans, because the three are not independent: "loading" and
 * "has results" must never both be true, and three flags cannot enforce that.
 */
export type SearchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: SearchResponse }
  | { status: 'empty'; data: SearchResponse }
  | { status: 'error'; message: string; code: string }

export interface UseMovieSearch {
  state: SearchState
  isSearching: boolean
}

/**
 * Debounced, cancellable title search.
 *
 * **State is derived, not assigned.** The rendered state is computed during
 * render from two things: the current query key, and the single stored result
 * tagged with the key it belongs to. Nothing calls `setState` synchronously
 * inside the effect, which matters for two reasons beyond lint compliance - the
 * "loading" state appears on the very first render of a new query instead of one
 * render later, and there is no cascade of re-renders when the query resets to
 * idle.
 *
 * ```
 *   key is null            -> idle
 *   stored result is for a different key -> loading   (a newer query is in flight)
 *   otherwise              -> the stored state
 * ```
 *
 * Three further details do the real work:
 *
 * 1. **Cancellation.** Each request gets an `AbortController`, and the cleanup
 *    aborts the previous one when the query changes. Without this, typing
 *    "space" fires four requests whose responses can land out of order.
 * 2. **Sequence guarding.** A response already in flight cannot be cancelled, so
 *    a monotonically increasing token discards any late arrival. Belt and
 *    braces, because stale results overwriting fresh ones is the single most
 *    visible bug a search box can have.
 * 3. **Abort is not an error.** A superseded request produces silence, never the
 *    error state. Otherwise every keystroke would flash an error panel.
 */
export function useMovieSearch(query: string): UseMovieSearch {
  const trimmed = query.trim()

  /*
   * The query key. `null` means "nothing to search for": either the box is empty
   * or the text is under the minimum length. A single character matches 1,700+
   * of the 5,000 titles, so searching it wastes a request and returns a list
   * nobody reads. The status line in `SearchPanel` explains the minimum; this
   * is the same rule applied earlier, so the two cannot disagree.
   */
  const key = trimmed.length < SEARCH_MIN_LENGTH ? null : trimmed

  const [result, setResult] = useState<{ key: string; state: SearchState } | null>(
    null,
  )
  const controllerRef = useRef<AbortController | null>(null)
  const sequenceRef = useRef(0)

  useEffect(() => {
    if (key === null) return

    const controller = new AbortController()
    controllerRef.current = controller
    const sequence = ++sequenceRef.current

    searchMovies(key, undefined, controller.signal)
      .then((data) => {
        if (sequence !== sequenceRef.current) return
        setResult({
          key,
          state:
            data.results.length === 0
              ? { status: 'empty', data }
              : { status: 'ready', data },
        })
      })
      .catch((error: unknown) => {
        if (sequence !== sequenceRef.current) return
        if (isAbortError(error)) return
        if (isMovieMindError(error)) {
          setResult({
            key,
            state: { status: 'error', message: error.message, code: errorCode(error) },
          })
          return
        }
        setResult({
          key,
          state: {
            status: 'error',
            message: 'Something went wrong while searching.',
            code: 'unknown',
          },
        })
      })

    // Aborting on cleanup is what cancels the superseded request when `key`
    // changes, and what stops a response updating a gone component on unmount.
    return () => controller.abort()
  }, [key])

  useEffect(() => {
    return () => controllerRef.current?.abort()
  }, [])

  const state: SearchState =
    key === null
      ? { status: 'idle' }
      : result?.key === key
        ? result.state
        : { status: 'loading' }

  return { state, isSearching: state.status === 'loading' }
}
