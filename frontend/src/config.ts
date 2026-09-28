/**
 * Application configuration - the single place a URL or a tunable lives.
 *
 * **Why an empty default is correct here.** `API_BASE_URL` is `''` by default,
 * so the app requests `/api/v1/...` relative to its own origin. That is what the
 * Vite dev proxy forwards to the backend (`vite.config.ts`), and it is what the
 * test suite exercises: tests stub `fetch` and assert on relative paths, so a
 * hard-coded `http://localhost:8000` anywhere would break them immediately.
 *
 * This matters beyond convenience. The backend's CORS allowlist is four explicit
 * localhost origins and no wildcard (`docs/phase-05-api.md` sec 6). A frontend
 * that called the API by absolute URL from a browser would depend on that
 * allowlist; a same-origin relative path does not, so the app works unchanged
 * behind a reverse proxy or on a different port.
 *
 * **Deployment.** Set `VITE_API_BASE_URL` to the API's origin (e.g.
 * `https://api.example.test`) when the frontend is served from a different host
 * than the API. Leave it unset for same-origin, which is the default and the
 * recommended local setup. See `.env.example` and
 * `docs/phase-06-frontend.md` sec 4.
 */

/**
 * Origin of the MovieMind API, with no trailing slash. Empty means same-origin.
 *
 * Trailing slashes are stripped because `new URL('/api/v1/...', base)` and
 * string concatenation treat a trailing slash differently, and the resulting
 * `//api/v1` double slash is a 404 that looks like a proxy problem.
 */
export const API_BASE_URL: string = (
  (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, '')
)

/** URL prefix. Matches `API_PREFIX` in `moviemind/api/app.py`. */
export const API_PREFIX = '/api/v1'

/**
 * How long a single request may take before it is treated as failed.
 *
 * Generous, because the first call after a cold backend start pays the whole
 * catalogue load. 0.27 s measured on 5,000 films, so 10 s is ~37x headroom -
 * enough that a slow disk does not produce a false failure, short enough that a
 * genuinely dead backend does not leave the UI spinning forever.
 */
export const REQUEST_TIMEOUT_MS = 10_000

/**
 * Debounce interval for the search box, in milliseconds.
 *
 * The backend answers a title search in ~0.55 ms, so it could absorb far more
 * traffic than a human produces. The debounce is therefore not about protecting
 * the server - it is about not firing a request per keystroke, which flickers
 * the loading state and reorders results mid-type. 250 ms is below the ~300 ms
 * at which a debounce starts feeling laggy, and it collapses a normal
 * 150-200 WPM word into roughly one request.
 */
export const SEARCH_DEBOUNCE_MS = 250

/**
 * Minimum query length before a search is issued.
 *
 * Two characters is where a single keystroke stops being a meaningful
 * constraint on 5,000 titles. One character matches 1,700+ of them and produces
 * a result list nobody reads. Deliberately enforced in the UI as well as in the
 * client's empty-query guard, so the user gets an explanation instead of a
 * request that is refused.
 */
export const SEARCH_MIN_LENGTH = 2

/** Result count requested per search. Matches the backend's own default. */
export const SEARCH_LIMIT = 10

/** Default and maximum `k` for the recommendation request. */
export const DEFAULT_K = 10
export const MAX_K = 50

/**
 * How many `k` values the results panel offers.
 *
 * A short fixed list rather than a free numeric input: the backend rejects
 * anything above 50, and Phase 4 measured that a mean query only supplies ~12
 * candidates at the evidence threshold. Offering 5/10/20 is honest about that;
 * a spinner to 50 would only ever produce exhausted lists.
 */
export const K_OPTIONS = [5, 10, 20] as const
export type KOption = (typeof K_OPTIONS)[number]
