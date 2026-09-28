/**
 * Typed models for the MovieMind API.
 *
 * **These mirror the Python schemas field for field, in snake_case.** The
 * alternative — camelCasing at the boundary — means a hand-written mapping for
 * every field of every response, plus a second thing to keep in sync when the
 * backend adds a field. Mirroring means the wire format and this file can be
 * diffed against `moviemind/api/schemas.py` directly.
 *
 * Every field is optional-nullable where the backend says `X | None`. That
 * precision is the point: `title: string | null` forces a decision at each use
 * site, and the real corpus genuinely contains nulls (Phase 2 sec 9: 126 films
 * have `vote_average: 0.0`, 56 have no genres, 8 have no director). Rendering
 * "undefined" for a null rating would be inventing information.
 *
 * `strict` is on, so a field the backend adds is a compile error here rather
 * than a silent `undefined` at runtime. That is the intended direction of
 * failure: the contract should break loudly, not quietly.
 */

/** How a search result matched, best tier first. Mirrors `MatchKind`. */
export type MatchKind =
  | 'exact'
  | 'title_prefix'
  | 'original_title_prefix'
  | 'title_contains'

/** The engine's skip vocabulary. Mirrors `SkipReason`. */
export type SkipReason = 'below_min_score' | 'insufficient_shared_terms'

/** Mirrors `ErrorBody`. */
export interface ApiErrorBody {
  code: string
  message: string
  status: number
  details?: Record<string, unknown> | null
}

/** Mirrors `ErrorResponse` - the single error envelope. */
export interface ApiErrorResponse {
  error: ApiErrorBody
}

/** Mirrors `HealthResponse`. */
export interface HealthResponse {
  status: 'ok'
  service: string
  api_version: string
}

/** Mirrors `CatalogueStats`. */
export interface CatalogueStats {
  films: number
  dimensions: number
  nonzeros: number
  density_percent: number
  load_seconds: number
}

/** Mirrors `ReadyResponse`. */
export interface ReadyResponse {
  status: 'ready' | 'not_ready'
  reason: string | null
  catalogue: CatalogueStats | null
}

/** Mirrors `Attribution`. The notice is rendered verbatim, never reworded. */
export interface Attribution {
  notice: string
  source: string
  license: string
}

/** Mirrors `MetaResponse`. */
export interface MetaResponse {
  api_version: string
  representation: string
  config_fingerprint: string
  min_shared_terms: number
  similarity: string
  limits: {
    default_k: number
    max_k: number
    default_search_limit: number
    max_search_limit: number
    max_query_length: number
  }
  attribution: Attribution
  notes: string[]
}

/** Mirrors `SearchHit`. */
export interface SearchHit {
  movie_id: number
  title: string | null
  original_title: string | null
  release_year: number | null
  genres: string[]
  popularity: number | null
  match: MatchKind
}

/** Mirrors `SearchResponse`. */
export interface SearchResponse {
  query: string
  limit: number
  total_matches: number
  count: number
  results: SearchHit[]
}

/**
 * Mirrors `MovieDetail`.
 *
 * There is no `overview` field and no `tagline`, and that is a property of the
 * pipeline rather than an omission here. `data/processed/movies.jsonl` - the
 * only corpus the API serves - stores the tokenised `document`/`tokens`, not
 * the source prose, and Phase 4 pins that file's SHA-256. The overview exists
 * only in the gitignored raw snapshot. The UI therefore presents the indexed
 * evidence (`feature_counts`, `document_size`) instead of a synopsis, and does
 * not fabricate one. See docs/phase-06-frontend.md.
 */
export interface MovieDetail {
  movie_id: number
  title: string | null
  original_title: string | null
  release_year: number | null
  release_date: string | null
  runtime_minutes: number | null
  original_language: string | null
  genres: string[]
  collection_name: string | null
  popularity: number | null
  vote_average: number | null
  vote_count: number | null
  adult: boolean
  feature_counts: Record<string, number>
  document_size: number | null
  recommendable: boolean
}

/** Mirrors `RecommendationItem`. */
export interface RecommendationItem {
  rank: number
  movie_id: number
  title: string | null
  /** Cosine similarity, nominally in [0, 1]. Not a percentage or a quality grade. */
  score: number
  year: number | null
  genres: string[]
  popularity: number | null
  vote_average: number | null
  vote_count: number | null
  collection_name: string | null
  /** Non-zero indexed terms shared with the query. The evidence behind `score`. */
  shared_terms: number
}

/** Mirrors `QueryMovie`. */
export interface QueryMovie {
  movie_id: number
  title: string | null
}

/** Mirrors `EvidenceInfo`. */
export interface EvidenceInfo {
  min_shared_terms: number
  requested_k: number
  returned: number
  candidates_examined: number
  candidates_rejected: number
  exhausted: boolean
}

/** Mirrors `SkippedCandidate`. */
export interface SkippedCandidate {
  movie_id: number
  reason: SkipReason
}

/** Mirrors `RecommendationResponse`. */
export interface RecommendationResponse {
  query: QueryMovie
  k: number
  similarity: string
  evidence: EvidenceInfo
  recommendations: RecommendationItem[]
  skipped: SkippedCandidate[]
}
