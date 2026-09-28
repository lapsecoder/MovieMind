import { useEffect, useState } from 'react'

import { getMeta, getReady } from '../api/client'
import { ApiRequestError, isAbortError } from '../api/errors'
import type { MetaResponse } from '../api/types'

export interface AboutData {
  /** Null until `/meta` resolves, and permanently null if it failed. */
  meta: MetaResponse | null
  /** True when /meta could not be loaded and a built-in fallback is in use. */
  usedFallback: boolean
}

/**
 * The notice the app must display, used only if the backend is unreachable.
 *
 * **This is a licence obligation, not a convenience.** TMDb's terms require the
 * notice verbatim, and `docs/phase-05-api.md` sec 8 pins the exact sentence. The
 * primary source is always `GET /meta`, so the wording travels with the backend
 * and cannot drift from it. This constant exists for the one case where it
 * cannot: the API is down, and the user would otherwise see an About panel with
 * no attribution at all - which is the worst possible moment to fail a licence
 * requirement.
 *
 * It is byte-identical to `TMDB_NOTICE` in `moviemind/api/service.py` and to
 * `test_tmdb_notice_is_verbatim` in the backend suite.
 */
export const FALLBACK_TMDB_NOTICE =
  'This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.'

/**
 * Load the locked configuration, limits, caveats, and TMDb attribution.
 *
 * Fetched once on mount and never refetched: none of it changes while the app
 * is open, and it is needed for the footer and the About panel rather than for
 * the main flow, so a failure here must not block searching.
 */
export function useAbout(): AboutData {
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    getMeta(controller.signal)
      .then((data) => {
        setMeta(data)
        setFailed(false)
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return
        // A failure here is non-fatal by design. The app still searches and
        // recommends; only the About panel falls back to the built-in notice.
        setFailed(true)
      })
    return () => controller.abort()
  }, [])

  return { meta, usedFallback: meta === null && failed }
}

/**
 * Catalogue readiness, for the header pill.
 *
 * `GET /ready` is polled once on mount rather than continuously: the catalogue
 * is built at backend startup, so its readiness changes before the first request
 * succeeds or long after. A polling interval would be traffic with no purpose.
 *
 * The 503 case is the interesting one. When the corpus has not loaded, `/ready`
 * answers **503** with a `not_ready` envelope, so the call rejects. That is a
 * legitimate state rather than a failure, and it is mapped to `not_ready` so the
 * user sees "catalogue not loaded" instead of a red error. A transport failure
 * is mapped separately to `unreachable`, because those need different words.
 */
export type Readiness = 'checking' | 'ready' | 'not_ready' | 'unreachable'

export function useReadiness(): Readiness {
  const [readiness, setReadiness] = useState<Readiness>('checking')

  useEffect(() => {
    const controller = new AbortController()
    getReady(controller.signal)
      .then((data) => setReadiness(data.status === 'ready' ? 'ready' : 'not_ready'))
      .catch((error: unknown) => {
        if (isAbortError(error)) return
        if (error instanceof ApiRequestError) {
          setReadiness('not_ready')
          return
        }
        setReadiness('unreachable')
      })
    return () => controller.abort()
  }, [])

  return readiness
}
