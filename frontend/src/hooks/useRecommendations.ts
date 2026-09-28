import { useEffect, useRef, useState } from 'react'

import { getRecommendations } from '../api/client'
import { errorCode, isAbortError, isMovieMindError } from '../api/errors'
import { DEFAULT_K } from '../config'
import type { RecommendationResponse } from '../api/types'

export type RecommendationsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: RecommendationResponse }
  | { status: 'error'; message: string; code: string }

/**
 * Load recommendations for the selected film.
 *
 * Requests are deliberately **not** fired until a film is selected: there is no
 * default query, because a recommendation list without a query film has no
 * meaning in this product.
 *
 * The key includes `k`, so changing the result count is a new request rather
 * than a client-side re-slice of the previous page. The results the engine
 * returned for `k=5` are not a prefix of what it would return for `k=10` in
 * general, because the engine re-ranks over a wider candidate set - so slicing
 * the smaller response would be a different answer wearing the same number.
 */
export function useRecommendations(
  movieId: number | null,
  k: number = DEFAULT_K,
): RecommendationsState {
  const key = movieId === null ? null : `${movieId}:${k}`

  const [result, setResult] = useState<{
    key: string
    state: RecommendationsState
  } | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const sequenceRef = useRef(0)

  useEffect(() => {
    if (key === null || movieId === null) return

    const controller = new AbortController()
    controllerRef.current = controller
    const sequence = ++sequenceRef.current

    getRecommendations(movieId, k, controller.signal)
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
            message: 'Something went wrong loading recommendations.',
            code: 'unknown',
          },
        })
      })

    return () => controller.abort()
  }, [key, movieId, k])

  useEffect(() => {
    return () => controllerRef.current?.abort()
  }, [])

  return key === null
    ? { status: 'idle' }
    : result?.key === key
      ? result.state
      : { status: 'loading' }
}
