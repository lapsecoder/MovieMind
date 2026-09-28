import { useEffect, useRef, useState } from 'react'

import { getMovie } from '../api/client'
import { errorCode, isAbortError, isMovieMindError } from '../api/errors'
import type { MovieDetail } from '../api/types'

export type DetailsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: MovieDetail }
  | { status: 'error'; message: string; code: string }

/**
 * Load one film's metadata.
 *
 * Uses the same derived-state shape as `useMovieSearch`: the result is tagged
 * with the id it was fetched for, and the rendered state is computed from the
 * current id and that stored result. Nothing is assigned synchronously inside
 * the effect, so selecting a film shows `loading` immediately instead of
 * briefly continuing to display the previous film.
 *
 * The stored id is also what makes a *stale* response harmless: a details
 * request for a film the user has already moved away from is discarded even if
 * it resolves after the newer one.
 */
export function useMovieDetails(movieId: number | null): DetailsState {
  const key = movieId === null ? null : String(movieId)

  const [result, setResult] = useState<{
    key: string
    state: DetailsState
  } | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const sequenceRef = useRef(0)

  useEffect(() => {
    if (key === null || movieId === null) return

    const controller = new AbortController()
    controllerRef.current = controller
    const sequence = ++sequenceRef.current

    getMovie(movieId, controller.signal)
      .then((data) => {
        if (sequence !== sequenceRef.current) return
        setResult({ key, state: { status: 'ready', data } })
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
            message: 'Something went wrong loading this film.',
            code: 'unknown',
          },
        })
      })

    return () => controller.abort()
  }, [key, movieId])

  useEffect(() => {
    return () => controllerRef.current?.abort()
  }, [])

  return key === null
    ? { status: 'idle' }
    : result?.key === key
      ? result.state
      : { status: 'loading' }
}
