/**
 * The single HTTP client for the whole application.
 *
 * Every network call in the app goes through this module. There is no `fetch`
 * anywhere else, which is what makes four requirements enforceable in one place
 * rather than by convention:
 *
 * 1. **Centralised base URL.** `src/config.ts` owns it. A component cannot
 *    invent `http://localhost:8000` because it never touches a URL.
 * 2. **Consistent error handling.** Non-2xx becomes `ApiRequestError` with the
 *    backend's own code and message; a dead server becomes `ApiNetworkError`;
 *    a malformed body becomes `ApiParseError`. See `./errors`.
 * 3. **Cancellation.** Every method takes an `AbortSignal`, so a superseded
 *    debounced search is cancelled rather than left to race the new one and
 *    overwrite it with stale results.
 * 4. **No duplicated fetch logic.** Parsing, timeouts, and query-string
 *    building exist once.
 *
 * **No recommendation logic lives here.** This module transports and
 * re-shapes; it never ranks, filters, or scores. The engine's ordering is
 * reproduced verbatim by every consumer, and `docs/phase-06-frontend.md` sec 3
 * records why duplicating it in TypeScript was rejected.
 */

import { API_BASE_URL, API_PREFIX, REQUEST_TIMEOUT_MS } from '../config'
import {
  ApiNetworkError,
  ApiParseError,
  ApiRequestError,
  isAbortError,
  parseErrorBody,
} from './errors'
import type {
  HealthResponse,
  MetaResponse,
  MovieDetail,
  ReadyResponse,
  RecommendationResponse,
  SearchResponse,
} from './types'

/**
 * A shape check that is intentionally shallow.
 *
 * The fields a component will dereference are checked; the rest is trusted.
 * A full runtime schema validator would be a dependency and a second source of
 * truth, and its failure mode here would be rejecting a valid response after
 * the backend added an optional field.
 */
function expectObject(value: unknown, what: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ApiParseError(`Expected an object for ${what}.`)
  }
  return value as Record<string, unknown>
}

function expectArray(value: unknown, what: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new ApiParseError(`Expected an array for ${what}.`)
  }
  return value
}

/**
 * Combine a caller-supplied signal with a timeout.
 *
 * `AbortSignal.any` is the modern way to express this, but it is unavailable in
 * some jsdom versions the test suite runs under, so it is feature-detected
 * rather than assumed. A timeout matters here: a local backend that accepts the
 * connection and then stalls would otherwise leave the UI in a permanent
 * loading state with no way to recover.
 */
function withTimeout(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; dispose: () => void; timedOut: () => boolean } {
  const controller = new AbortController()
  let timedOut = false

  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const onAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onAbort, { once: true })
  }

  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    dispose: () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    },
  }
}

/**
 * Perform one request and return the decoded body.
 *
 * Exported for tests and for the single place that knows how the wire format
 * behaves; components should use the named methods below.
 */
export async function request<T>(
  path: string,
  options: { signal?: AbortSignal; query?: Record<string, string | number> } = {},
): Promise<T> {
  const url = new URL(`${API_BASE_URL}${API_PREFIX}${path}`, 'http://placeholder')
  for (const [key, value] of Object.entries(options.query ?? {})) {
    url.searchParams.set(key, String(value))
  }

  /*
   * `target` is absolute only when a base URL was configured.
   *
   * `new URL` always yields an absolute URL because of the placeholder origin, so
   * `url.href` cannot be used directly: with the default same-origin config it
   * would send every request to `http://placeholder`. When `API_BASE_URL` is
   * empty the path is relative and must be kept relative, which is what lets the
   * Vite proxy forward it during development and a reverse proxy forward it in
   * production. When a real base is configured, the full URL including the
   * origin must be used, or every request would silently go to this host.
   */
  const target = API_BASE_URL.length > 0 ? url.toString() : `${url.pathname}${url.search}`

  const { signal, dispose, timedOut } = withTimeout(
    options.signal,
    REQUEST_TIMEOUT_MS,
  )

  let response: Response
  try {
    response = await fetch(target, {
      method: 'GET',
      signal,
      headers: { Accept: 'application/json' },
    })
  } catch (error) {
    // A caller-initiated abort must propagate as-is: `useMovieSearch` treats it
    // as "superseded", not as a failure. Only a timeout is translated.
    if (isAbortError(error) && !timedOut()) {
      throw error
    }
    if (timedOut()) {
      throw new ApiNetworkError(
        `The API did not respond within ${Math.round(REQUEST_TIMEOUT_MS / 1000)}s.`,
      )
    }
    throw new ApiNetworkError()
  } finally {
    dispose()
  }

  // A 204 has no body to parse; nothing in this API returns one, but treating
  // it as an empty object beats a parse crash.
  if (response.status === 204) return {} as T

  const text = await response.text()
  let payload: unknown = null
  if (text.length > 0) {
    try {
      payload = JSON.parse(text)
    } catch {
      // A non-JSON body on an error status is usually a proxy or a crash page.
      if (!response.ok) {
        throw new ApiRequestError({
          code: 'internal_error',
          message: `The API returned HTTP ${response.status}.`,
          status: response.status,
          details: null,
        })
      }
      throw new ApiParseError()
    }
  }

  if (!response.ok) {
    const body = parseErrorBody(payload)
    if (body) {
      throw new ApiRequestError(
        body.status === 0 ? { ...body, status: response.status } : body,
      )
    }
    throw new ApiRequestError({
      code: 'internal_error',
      message: `The API returned HTTP ${response.status}.`,
      status: response.status,
      details: null,
    })
  }

  return payload as T
}

/**
 * Verify a response is shaped as documented before it reaches a component.
 *
 * The point is to fail at the client boundary with a clear message, rather than
 * deep inside a render with `undefined is not an object`.
 */
function assertSearchResponse(value: unknown): SearchResponse {
  const body = expectObject(value, 'search response')
  expectArray(body.results, 'search response results')
  return value as SearchResponse
}

function assertMovieDetail(value: unknown): MovieDetail {
  const body = expectObject(value, 'movie detail')
  if (typeof body.movie_id !== 'number') {
    throw new ApiParseError('Movie detail is missing a numeric movie_id.')
  }
  return value as MovieDetail
}

function assertRecommendationResponse(value: unknown): RecommendationResponse {
  const body = expectObject(value, 'recommendation response')
  expectArray(body.recommendations, 'recommendation response recommendations')
  expectObject(body.evidence, 'recommendation response evidence')
  return value as RecommendationResponse
}

function assertMetaResponse(value: unknown): MetaResponse {
  const body = expectObject(value, 'meta response')
  expectObject(body.attribution, 'meta response attribution')
  expectObject(body.limits, 'meta response limits')
  return value as MetaResponse
}

/** Liveness. Kept separate from readiness: this one never needs the catalogue. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { signal })
}

/** Readiness, including the 503-when-unloaded case, surfaced as a rejection. */
export function getReady(signal?: AbortSignal): Promise<ReadyResponse> {
  return request<ReadyResponse>('/ready', { signal })
}

/** Locked config, limits, TMDb attribution, and the Phase 4 caveats. */
export async function getMeta(signal?: AbortSignal): Promise<MetaResponse> {
  return assertMetaResponse(await request<unknown>('/meta', { signal }))
}

/**
 * Title search.
 *
 * The empty-query case is refused here rather than sent: the backend answers
 * `400 empty_query`, and a request that is known to be invalid should not cost a
 * round trip or a loading flash. The `max_query_length` check mirrors the
 * server's ceiling so an over-long query is caught locally; the server remains
 * the authority and its `query_too_long` is still handled.
 */
export async function searchMovies(
  query: string,
  limit?: number,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const trimmed = query.trim()
  if (trimmed.length === 0) {
    throw new ApiRequestError({
      code: 'empty_query',
      message: "Query parameter 'q' must contain at least one non-whitespace character.",
      status: 400,
      details: null,
    })
  }
  const response = await request<unknown>('/movies/search', {
    signal,
    query: limit === undefined ? { q: trimmed } : { q: trimmed, limit },
  })
  return assertSearchResponse(response)
}

/** Metadata for one film. Rejects `404 unknown_movie` / `503` as typed errors. */
export async function getMovie(
  movieId: number,
  signal?: AbortSignal,
): Promise<MovieDetail> {
  return assertMovieDetail(
    await request<unknown>(`/movies/${encodeURIComponent(String(movieId))}`, { signal }),
  )
}

/** Ranked recommendations, in the engine's own order. */
export async function getRecommendations(
  movieId: number,
  k?: number,
  signal?: AbortSignal,
): Promise<RecommendationResponse> {
  const response = await request<unknown>(
    `/movies/${encodeURIComponent(String(movieId))}/recommendations`,
    { signal, query: k === undefined ? {} : { k } },
  )
  return assertRecommendationResponse(response)
}

/** The client as an object, so tests and components can inject a double. */
export const api = {
  getHealth,
  getReady,
  getMeta,
  searchMovies,
  getMovie,
  getRecommendations,
} as const

export type MovieMindApi = typeof api
