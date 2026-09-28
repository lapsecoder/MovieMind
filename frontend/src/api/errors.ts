/**
 * Error handling for the API client.
 *
 * The backend guarantees one envelope for every failure
 * (`docs/phase-05-api.md` sec 4):
 *
 *     { "error": { code, message, status, details? } }
 *
 * `code` is the stable, machine-readable part and is what components branch on.
 * `message` is written for humans and is passed through untouched so the user
 * sees the backend's own explanation rather than a paraphrase of it.
 *
 * Three failure classes are distinguished, because they need different UI:
 *
 * - `ApiRequestError`  - the server answered, with a 4xx/5xx. The user can act.
 * - `ApiNetworkError`  - the server never answered. Retrying may work; the
 *   backend is probably not running.
 * - `ApiParseError`    - the server answered with something that is not the
 *   documented shape. A bug or a proxy; not actionable by the user.
 *
 * A request the caller aborted is *not* an error state. `AbortController`
 * cancellation is normal during debounced search, and it is surfaced as a
 * rejected promise carrying an `AbortError` so the caller can ignore it. Turning
 * it into a visible "something went wrong" would flash an error panel on every
 * keystroke.
 */

import type { ApiErrorBody, ApiErrorResponse } from './types'

/** Stable error codes from `moviemind/api/errors.py`. */
export const ERROR_CODES = {
  emptyQuery: 'empty_query',
  queryTooLong: 'query_too_long',
  invalidK: 'invalid_k',
  invalidLimit: 'invalid_limit',
  unknownMovie: 'unknown_movie',
  insufficientFeatures: 'insufficient_features',
  validationError: 'validation_error',
  catalogueUnavailable: 'catalogue_unavailable',
  internalError: 'internal_error',
  notFound: 'not_found',
  methodNotAllowed: 'method_not_allowed',
} as const

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES]

/**
 * True when the failure was a cancelled request rather than a real problem.
 *
 * Checks the `name` rather than only `instanceof DOMException`, because the
 * rejection value is not guaranteed to be a `DOMException`: Node's undici throws
 * an `Error` with `name === 'AbortError'`, a `fetch` polyfill may do the same,
 * and jsdom's own implementation has changed across versions. All of those must
 * be treated as cancellation, or every debounced keystroke would show an error.
 */
export function isAbortError(error: unknown): boolean {
  if (typeof DOMException !== 'undefined' && error instanceof DOMException) {
    return error.name === 'AbortError'
  }
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  )
}

/** Base class, so one `catch` can tell API failures from programming errors. */
export class MovieMindError extends Error {
  constructor(message: string) {
    super(message)
    this.name = new.target.name
  }
}

/** The server responded with a non-2xx status and a valid error envelope. */
export class ApiRequestError extends MovieMindError {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown> | null

  constructor(body: ApiErrorBody) {
    super(body.message)
    this.code = body.code
    this.status = body.status
    this.details = body.details ?? null
  }

  /**
   * True when retrying the identical request could plausibly succeed.
   *
   * A 503 means the catalogue failed to load, which a server restart fixes. A
   * 404 or a 422 will fail identically forever, so retrying it just spins.
   */
  get retryable(): boolean {
    return this.status >= 500
  }

  /**
   * True when the failure is the user's input rather than the system's.
   *
   * Drives whether the UI offers a correction or an apology. A 400
   * `empty_query` is the user's; a 503 is nobody's.
   */
  get isUserError(): boolean {
    return this.status >= 400 && this.status < 500
  }
}

/** The request never produced a response: backend down, CORS, DNS, offline. */
export class ApiNetworkError extends MovieMindError {
  constructor(message = 'Could not reach the MovieMind API.') {
    super(message)
  }
}

/** A 2xx response whose body did not match the documented shape. */
export class ApiParseError extends MovieMindError {
  constructor(message = 'The API returned an unexpected response shape.') {
    super(message)
  }
}

/**
 * Narrow an unknown `catch` binding to something this module defined.
 *
 * Components use this so an unexpected runtime error in a component does not
 * get rendered as "the API is unreachable".
 */
export function isMovieMindError(error: unknown): error is MovieMindError {
  return error instanceof MovieMindError
}

/**
 * The stable backend code, when the failure carried one.
 *
 * Returns `'unknown'` for a network or parse failure, which have no code because
 * the server never produced an envelope. Callers that want to vary the UI by
 * cause should compare against `ERROR_CODES`, and `'unknown'` is the catch-all
 * that renders the message as-is.
 */
export function errorCode(error: unknown): string {
  return error instanceof ApiRequestError ? error.code : 'unknown'
}

/**
 * Pull the error envelope out of a response body.
 *
 * Returns `null` for a body that is not an envelope, so the caller can fall back
 * to a status-derived message. A proxy or an error page returns HTML, and
 * `JSON.parse` on that throws - which must not become an unhandled rejection.
 */
export function parseErrorBody(payload: unknown): ApiErrorBody | null {
  if (typeof payload !== 'object' || payload === null) return null
  const envelope = payload as Partial<ApiErrorResponse>
  const body = envelope.error
  if (typeof body !== 'object' || body === null) return null
  if (typeof body.code !== 'string' || typeof body.message !== 'string') return null
  return {
    code: body.code,
    message: body.message,
    status: typeof body.status === 'number' ? body.status : 0,
    details: (body.details as Record<string, unknown> | undefined) ?? null,
  }
}
