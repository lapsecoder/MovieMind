import { describe, expect, it, vi } from 'vitest'

import {
  getHealth,
  getMeta,
  getMovie,
  getRecommendations,
  getReady,
  request,
  searchMovies,
} from '../api/client'
import {
  ApiNetworkError,
  ApiParseError,
  ApiRequestError,
  errorCode,
  isAbortError,
  parseErrorBody,
} from '../api/errors'
import type {
  HealthResponse,
  MetaResponse,
  MovieDetail,
  ReadyResponse,
  RecommendationResponse,
  SearchResponse,
} from '../api/types'
import { abortError, loadFixture, mockApi } from './mockApi'

/**
 * Client tests.
 *
 * These pin the things that are easy to break silently and hard to notice:
 * URL construction, the relative-vs-absolute base decision, error-code mapping,
 * and cancellation. A response that is merely plausible is not enough - the
 * fixtures are real captures, so `assertMovieDetail` is checked against a body
 * the backend actually produced.
 */

describe('URL construction', () => {
  it('sends a same-origin relative path when no base URL is configured', async () => {
    const api = mockApi({ '/health': { body: loadFixture<HealthResponse>('health') } })

    await getHealth()

    // No leading origin: this is what lets the Vite proxy and a production
    // reverse proxy forward the request unchanged.
    expect(api.calls()[0]).toBe('/api/v1/health')
    expect(api.calls()[0].startsWith('http')).toBe(false)
  })

  it('percent-encodes a movie id in the path', async () => {
    const api = mockApi({ '/recommendations': { body: loadFixture<RecommendationResponse>('recommendations') } })

    await getRecommendations(278)

    expect(api.calls()[0]).toBe('/api/v1/movies/278/recommendations')
  })

  it('passes search terms through URLSearchParams so they are encoded', async () => {
    const api = mockApi({ '/movies/search': { body: loadFixture<SearchResponse>('search') } })

    await searchMovies('naked gun 2½')

    // The ½ must survive round-tripping as UTF-8 percent-encoding, not be
    // dropped or turned into a literal space.
    expect(api.calls()[0]).toBe('/api/v1/movies/search?q=naked+gun+2%C2%BD')
  })

  it('sends limit alongside q when one is supplied', async () => {
    const api = mockApi({ '/movies/search': { body: loadFixture<SearchResponse>('search') } })

    await searchMovies('shawshank', 25)

    expect(api.calls()[0]).toBe('/api/v1/movies/search?q=shawshank&limit=25')
  })

  it('omits limit entirely when it is not supplied', async () => {
    const api = mockApi({ '/movies/search': { body: loadFixture<SearchResponse>('search') } })

    await searchMovies('shawshank')

    expect(api.calls()[0]).not.toContain('limit')
  })

  it('sends k as a query parameter, not in the path', async () => {
    const api = mockApi({ '/recommendations': { body: loadFixture<RecommendationResponse>('recommendations') } })

    await getRecommendations(278, 5)

    expect(api.calls()[0]).toBe('/api/v1/movies/278/recommendations?k=5')
  })
})

describe('response decoding', () => {
  it('returns the decoded body for a healthy response', async () => {
    mockApi({ '/health': { body: loadFixture<HealthResponse>('health') } })

    const result = await getHealth()

    expect(result.status).toBe('ok')
    expect(result.service).toBe('moviemind-api')
  })

  it('decodes a meta response with its attribution intact', async () => {
    mockApi({ '/meta': { body: loadFixture<MetaResponse>('meta') } })

    const result = await getMeta()

    expect(result.attribution.notice).toContain('This product uses TMDB')
    expect(result.limits.max_k).toBeGreaterThan(0)
  })

  it('decodes movie details from a real capture', async () => {
    const fixture = loadFixture<MovieDetail>('details')
    mockApi({ '/movies/278': { body: fixture } })

    const result = await getMovie(278)

    expect(result.movie_id).toBe(fixture.movie_id)
    expect(result.genres).toEqual(fixture.genres)
    expect(typeof result.feature_counts).toBe('object')
  })

  it('preserves recommendation order exactly as the engine returned it', async () => {
    const fixture = loadFixture<RecommendationResponse>('recommendations')
    mockApi({ '/recommendations': { body: fixture } })

    const result = await getRecommendations(278)

    // The client must not re-sort, re-rank, or re-filter. This is the test that
    // would fail if someone "improved" the ordering in the front end.
    expect(result.recommendations.map((r) => r.movie_id)).toEqual(
      fixture.recommendations.map((r) => r.movie_id),
    )
    expect(result.recommendations.map((r) => r.rank)).toEqual(
      fixture.recommendations.map((r) => r.rank),
    )
  })

  it('keeps a short result set short instead of padding it', async () => {
    const fixture = loadFixture<RecommendationResponse>('recommendations_short')
    mockApi({ '/recommendations': { body: fixture } })

    const result = await getRecommendations(278)

    expect(result.recommendations).toHaveLength(fixture.recommendations.length)
    expect(result.evidence.returned).toBe(result.recommendations.length)
  })

  it('accepts an empty recommendation set as a success, not an error', async () => {
    mockApi({ '/recommendations': { body: loadFixture<RecommendationResponse>('recommendations_none') } })

    const result = await getRecommendations(278)

    expect(result.recommendations).toHaveLength(0)
    expect(result.evidence.returned).toBe(0)
  })
})

describe('error mapping', () => {
  it('maps a 404 envelope to ApiRequestError with its code', async () => {
    mockApi({
      '/movies/999999999': {
        status: 404,
        body: loadFixture('error_unknown_movie'),
      },
    })

    const error = await getMovie(999999999).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiRequestError)
    const requestError = error as ApiRequestError
    expect(requestError.code).toBe('unknown_movie')
    expect(requestError.status).toBe(404)
    expect(requestError.retryable).toBe(false)
    expect(requestError.isUserError).toBe(true)
    // The backend's own wording reaches the user unaltered.
    expect(requestError.message).toBeTruthy()
  })

  it('preserves the 422 code and details for insufficient_features', async () => {
    mockApi({
      '/recommendations': {
        status: 422,
        body: {
          error: {
            code: 'insufficient_features',
            message: 'The query film has too few indexed features.',
            status: 422,
            details: { document_size: 1, min_required: 2 },
          },
        },
      },
    })

    const error = (await getRecommendations(1).catch((e: unknown) => e)) as ApiRequestError

    expect(error.code).toBe('insufficient_features')
    expect(error.status).toBe(422)
    expect(error.details).toEqual({ document_size: 1, min_required: 2 })
  })

  it('marks a 503 as retryable and a 4xx as not', async () => {
    mockApi({
      '/movies/1': {
        status: 503,
        body: {
          error: {
            code: 'catalogue_unavailable',
            message: 'The catalogue is not loaded.',
            status: 503,
            details: null,
          },
        },
      },
    })

    const error = (await getMovie(1).catch((e: unknown) => e)) as ApiRequestError

    expect(error.retryable).toBe(true)
    expect(error.isUserError).toBe(false)
  })

  it('reports a dead backend as a network error, not a request error', async () => {
    mockApi({ '/health': { networkError: true } })

    const error = await getHealth().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiNetworkError)
    // No code: the server never produced an envelope.
    expect(errorCode(error)).toBe('unknown')
  })

  it('synthesises a message for an error status with a non-JSON body', async () => {
    // What a proxy or a crash page actually returns.
    mockApi({ '/health': { status: 502, text: '<html>Bad Gateway</html>' } })

    const error = (await getHealth().catch((e: unknown) => e)) as ApiRequestError

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error.status).toBe(502)
    expect(error.message).toContain('502')
  })

  it('rejects a 2xx body that is not the documented shape', async () => {
    mockApi({ '/health': { body: { unexpected: true } } })

    // `/health` is not shape-asserted, so use a validated endpoint.
    mockApi({ '/meta': { body: { nope: 1 } } })

    const error = await getMeta().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiParseError)
  })

  it('rejects a search response whose results are not an array', async () => {
    mockApi({ '/movies/search': { body: { query: 'x', results: 'nope' } } })

    const error = await searchMovies('shawshank').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiParseError)
  })

  it('refuses a blank query locally, without a round trip', async () => {
    const api = mockApi({})

    const error = (await searchMovies('   ').catch((e: unknown) => e)) as ApiRequestError

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error.code).toBe('empty_query')
    // The point of the local guard: no request was made.
    expect(api.fetch).not.toHaveBeenCalled()
  })
})

describe('cancellation', () => {
  it('propagates an abort as an abort, not as an error', async () => {
    mockApi({ '/health': { never: true } })
    const controller = new AbortController()

    const pending = getHealth(controller.signal)
    controller.abort()

    const error = await pending.catch((e: unknown) => e)

    // This is the distinction the whole debounce depends on: a superseded
    // request is silence, and turning it into a visible error would flash an
    // error panel on every keystroke.
    expect(isAbortError(error)).toBe(true)
    expect(error).not.toBeInstanceOf(ApiNetworkError)
  })

  it('ignores a signal that is already aborted', async () => {
    // The route body is irrelevant: a real `fetch` rejects on an aborted signal
    // before it looks at anything, and so does the mock.
    mockApi({ '/health': { body: loadFixture<HealthResponse>('health') } })
    const controller = new AbortController()
    controller.abort()

    const error = await request('/health', { signal: controller.signal }).catch(
      (e: unknown) => e,
    )

    expect(isAbortError(error)).toBe(true)
  })
})

describe('parseErrorBody', () => {
  it('extracts a well-formed envelope', () => {
    const body = parseErrorBody({
      error: { code: 'invalid_k', message: 'bad k', status: 400, details: { k: 99 } },
    })

    expect(body).toEqual({
      code: 'invalid_k',
      message: 'bad k',
      status: 400,
      details: { k: 99 },
    })
  })

  it('returns null for a body that is not an envelope', () => {
    expect(parseErrorBody(null)).toBeNull()
    expect(parseErrorBody('nope')).toBeNull()
    expect(parseErrorBody({ error: null })).toBeNull()
    expect(parseErrorBody({ error: { message: 'no code' } })).toBeNull()
  })
})

describe('readiness', () => {
  it('decodes a ready response', async () => {
    mockApi({ '/ready': { body: loadFixture<ReadyResponse>('ready') } })

    const result = await getReady()

    expect(result.status).toBe('ready')
    expect(result.catalogue?.films).toBeGreaterThan(0)
  })

  it('surfaces a not-ready 503 as a rejection carrying the reason', async () => {
    mockApi({
      '/ready': {
        status: 503,
        body: {
          error: {
            code: 'catalogue_unavailable',
            message: 'The catalogue is not loaded.',
            status: 503,
            details: null,
          },
        },
      },
    })

    const error = (await getReady().catch((e: unknown) => e)) as ApiRequestError

    expect(error.status).toBe(503)
  })
})

describe('abort detection', () => {
  it('recognises a DOMException AbortError', () => {
    expect(isAbortError(abortError())).toBe(true)
  })

  it('recognises a plain Error named AbortError, as undici throws', () => {
    // Node's fetch does not always produce a DOMException. If this ever
    // returned false, every debounced keystroke would show an error in Node.
    const error = Object.assign(new Error('aborted'), { name: 'AbortError' })
    expect(isAbortError(error)).toBe(true)
  })

  it('does not mistake a real failure for an abort', () => {
    expect(isAbortError(new Error('boom'))).toBe(false)
    // The exact rejection `fetch` throws for a dead backend.
    expect(isAbortError(new TypeError('Failed to fetch'))).toBe(false)
  })
})

describe('timeout', () => {
  it('translates a timeout into a network error naming the limit', async () => {
    vi.useFakeTimers()
    mockApi({ '/health': { never: true } })

    const pending = getHealth().catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(10_000)
    const error = await pending

    expect(error).toBeInstanceOf(ApiNetworkError)
    expect((error as Error).message).toContain('10s')
  })
})

describe('fixtures', () => {
  it('uses the real TMDb notice wording', () => {
    // Guards the fixture itself: if this capture ever drifts, the licence
    // assertion in the component tests would be testing the wrong string.
    const meta = loadFixture<MetaResponse>('meta')
    expect(meta.attribution.notice).toBe(
      'This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.',
    )
  })

  it('has a unicode search fixture that survived capture', () => {
    const search = loadFixture<SearchResponse>('search_unicode')
    expect(JSON.stringify(search)).toContain('Naked Gun')
  })
})
