import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { vi } from 'vitest'

import { API_PREFIX } from '../config'
import type { ApiErrorBody } from '../api/types'

/**
 * A fetch double backed by the fixtures captured from a running FastAPI.
 *
 * The fixtures are genuine responses, not hand-written approximations, so a test
 * passing here is evidence about the real payload shapes - including the ones
 * that are awkward, like null titles and 1,837-element skipped arrays.
 *
 * Routing is by pathname, so a test can assert the client built the right URL
 * without a real server.
 */

const here = dirname(fileURLToPath(import.meta.url))

export function loadFixture<T>(name: string): T {
  const path = join(here, 'fixtures', `${name}.json`)
  return JSON.parse(readFileSync(path, 'utf8')) as T
}

export interface RouteOptions {
  status?: number
  body?: unknown
  /** Return raw text instead of JSON, for malformed-body tests. */
  text?: string
  /** Reject with this instead of responding, for network-failure tests. */
  networkError?: boolean
  /** Hold the response open, so an abort can be observed mid-flight. */
  never?: boolean
  /**
   * Resolve this many milliseconds late, ignoring the abort signal.
   *
   * Needed to test out-of-order responses: a response that is already on the
   * wire cannot be cancelled, so this is the only way to reproduce a stale
   * result arriving after a fresh one.
   */
  delayMs?: number
}

export interface MockApi {
  fetch: ReturnType<typeof vi.fn>
  /** Paths requested so far, in order. Assert on these to check URL building. */
  calls: () => string[]
}

type Handler = (path: string) => RouteOptions | Promise<RouteOptions>

export type { Handler }

/**
 * Build a fetch mock from a path -> response map.
 *
 * Keys are matched by `endsWith`, so a test can write `/movies/search` without
 * repeating the `/api/v1` prefix. An unlisted path resolves to a 404 envelope
 * rather than hanging, which turns "the client called the wrong URL" into an
 * obvious failure instead of a timeout.
 */
export function mockApi(routes: Record<string, RouteOptions | Handler>): MockApi {
  const calls: string[] = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    /*
     * A real `fetch` rejects immediately when handed an already-aborted signal,
     * before any network work. Reproducing that here is not pedantry: the client
     * relies on this path to turn a pre-aborted request into a silent abort, and
     * a mock that ignored the signal would let a cancelled request appear to
     * succeed.
     */
    if (init?.signal?.aborted) {
      throw init.signal.reason ?? new DOMException('Aborted', 'AbortError')
    }

    const url = typeof input === 'string' ? input : input.toString()
    const parsed = new URL(url, 'http://test.local')
    const path = parsed.pathname
    calls.push(path + parsed.search)

    const suffix = path.startsWith(API_PREFIX)
      ? path.slice(API_PREFIX.length)
      : path

    const match = Object.keys(routes).find(
      (key) => suffix === key || suffix.endsWith(key),
    )

    /*
     * Read the route once into a local. An indexed access is re-evaluated on
     * every use, so TypeScript cannot carry the `typeof === 'function'`
     * narrowing across separate `routes[match]` references, and every field
     * access below would be an error on the union with `Handler`.
     */
    const entry: RouteOptions | Handler | undefined =
      match === undefined ? undefined : routes[match]

    if (entry === undefined) {
      return jsonResponse(
        {
          error: {
            code: 'not_found',
            message: `No mock route for ${suffix}`,
            status: 404,
            details: null,
          },
        },
        404,
      )
    }

    const resolved: RouteOptions =
      typeof entry === 'function' ? await entry(suffix) : entry

    if (resolved.networkError) {
      throw new TypeError('Failed to fetch')
    }

    if (resolved.never) {
      // Never settles on its own. A rejection is wired to the signal so an
      // aborted request can settle, which is what makes cancellation testable.
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(init.signal?.reason ?? new DOMException('Aborted', 'AbortError')),
        )
      })
    }

    if (resolved.text !== undefined) {
      return new Response(resolved.text, {
        status: resolved.status ?? 200,
        headers: { 'Content-Type': 'text/plain' },
      })
    }

    if (resolved.delayMs) {
      await new Promise((resolve) => setTimeout(resolve, resolved.delayMs))
    }

    return jsonResponse(resolved.body, resolved.status ?? 200)
  })

  vi.stubGlobal('fetch', fetchMock)
  return { fetch: fetchMock, calls: () => calls }
}

/** A JSON `Response` shaped the way FastAPI produces one. */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A typed error envelope response, matching `ErrorResponse`. */
export function errorResponse(
  code: string,
  message: string,
  status: number,
  details: Record<string, unknown> | null = null,
): Response {
  const body: { error: ApiErrorBody } = { error: { code, message, status, details } }
  return jsonResponse(body, status)
}

/** Convenience: an abort rejection shaped like a real `fetch` cancellation. */
export function abortError(): DOMException {
  return new DOMException('The operation was aborted.', 'AbortError')
}
