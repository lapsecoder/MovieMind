# Phase 5 - Local REST API

**Status:** complete. Six read-only endpoints over the locked Phase 4 engine,
331 tests passing, no behaviour of the engine changed.

**Scope.** This phase puts an HTTP surface in front of work that already existed.
It does not improve retrieval, change the selected configuration, rebuild the
corpus, add a database, add authentication, or add a frontend. Every
recommendation the API returns is the engine's, byte for byte, and
`tests/test_api.py::TestRealCatalogue::test_recommendations_match_the_engine_exactly`
asserts that against the engine's own result object.

---

## 1. Headline

| | |
|---|---|
| **Endpoints** | 6, all `GET`, all under `/api/v1` |
| **Startup** | 0.27 s to load and vectorise 5,000 films |
| **Query latency** | 1.0 ms engine-side median at `k=10`; 6.1 ms end-to-end over HTTP |
| **Catalogue served** | 5,000 films, 23,404 dimensions, 226,352 nnz (unchanged from Phase 4) |
| **Config fingerprint** | `af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7` — unchanged |
| **Tests** | 331 passing, 109 of them new in this phase |
| **Network calls** | none. No TMDb token is read, required, or accepted |
| **Writes** | none. The corpus is opened read-only and never rewritten |

### Running it

```bash
python -m uvicorn moviemind.api.app:app --port 8000
```

No configuration is required: every setting has a working default, and
`data/processed/movies.jsonl` is the default corpus path. Interactive docs are at
`/docs`, the schema at `/openapi.json`.

---

## 2. Layering, and why it is structural rather than conventional

```
routes (app.py)      validate -> delegate -> serialise.  No retrieval logic.
service (service.py) the only caller of Recommender. Imports no FastAPI.
engine (recommend.py)  Phase 4, unchanged.
```

The Phase 5 requirement was that the API expose the engine without forking it.
That is enforced by construction rather than by review: `app.py` never imports
`Recommender`, never touches the sparse matrix, and cannot filter or reorder
anything, because the only thing it can reach is `CatalogueService`. A future
change that duplicated a filter in a route would have nothing to duplicate from.

The service layer is the whole reason the engine is still testable without an app.
`moviemind/recommend.py` has no FastAPI import, so §2's "engine" tests run with no
web stack installed at all.

One structural consequence worth naming: **limits are enforced in the service, not
the routes.** The service is the layer that normalises a query and range-checks
`k`, so a single `Settings` object reaches both the enforcement and the
`/api/v1/meta` advertisement of it. These were briefly two objects, which would
have let `/meta` publish a `max_k` the routes ignored;
`test_published_limits_are_the_limits_actually_enforced` exists to catch exactly
that drift.

---

## 3. Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/health` | `200` always, even if the catalogue failed to load |
| GET | `/api/v1/ready` | `200` + catalogue stats, or `503` + reason |
| GET | `/api/v1/meta` | Locked config, fingerprint, limits, TMDb attribution, caveats |
| GET | `/api/v1/movies/search?q=&limit=` | Ranked title matches |
| GET | `/api/v1/movies/{movie_id}` | Metadata for one film |
| GET | `/api/v1/movies/{movie_id}/recommendations?k=` | Ranked recommendations + evidence |

`/api/v1/movies/search` is declared **before** `/api/v1/movies/{movie_id}` in
`app.py`. FastAPI matches routes in declaration order, so the reverse order would
capture the literal segment `search` as a `{movie_id}` and reject a valid search
as a non-integer id. `test_search_route_is_not_captured_by_movie_id` pins this,
because the failure it prevents looks like a search bug, not a routing bug.

### 3.1 Search covers titles only

`q` matches `title` and `original_title`, case-folded, with the tiers
`exact` → `title_prefix` → `original_title_prefix` → `title_contains`.

The corpus also holds cast and director names, so searching them is easy.
It is not offered because Phase 4 measured cast recall@10 at **0.157** inside
representation D (§5.4) — an actor search would return films the recommender
does not consider similar, which is a promise the engine does not keep. Genres
fail differently: 19 of them makes a genre query a filter, not a search. Both
omissions are recorded in `/api/v1/meta`'s `notes` rather than hidden.

Ordering is `(tier, normalised title, movie_id)`. The trailing id is what makes
it deterministic: two films can normalise to the same string, and without it
their order would depend on corpus position.

### 3.2 Details hides the representation

`/api/v1/movies/{movie_id}` returns metadata plus `feature_counts` (per-field term
*counts*) and `document_size`. It never returns `tokens` or the sparse `document`
— the internal representation is not part of the contract.
`test_details_never_exposes_raw_representation` asserts the absence, since a
response that grew a `tokens` field would leak the index into the client's hands
and make it a de facto second source of truth.

`recommendable: false` is reported here rather than discovered as a `422` later,
so a client can grey out a film before the user clicks it.

---

## 4. Errors

One envelope, no exceptions:

```json
{"error": {"code": "unknown_movie", "message": "...", "status": 404, "details": {}}}
```

| Code | Status | When |
|---|---|---|
| `empty_query` | 400 | `q` is empty or whitespace only |
| `query_too_long` | 400 | `q` exceeds `max_query_length` |
| `unknown_movie` | 404 | well-formed TMDb id, not in the catalogue |
| `insufficient_features` | 422 | the film has an all-zero feature vector |
| `invalid_k` | 422 | `k` outside `1..max_k` |
| `invalid_limit` | 422 | `limit` outside `1..max_search_limit` |
| `validation_error` | 422 | non-integer id or `k`, missing `q` |
| `catalogue_unavailable` | 503 | the catalogue did not load at startup |
| `not_found` / `method_not_allowed` | 404 / 405 | unknown path, non-GET verb |
| `internal_error` | 500 | a bug; the detail is logged, never returned |

Three distinctions are deliberate:

- **A malformed query is not an empty result.** `q=""` is `400 empty_query`;
  a search that genuinely matched nothing is `200` with `results: []` and
  `total_matches: 0`. "You asked something invalid" and "nothing matched" are
  different facts and a client needs to tell them apart.
- **A sparse film is `422`, not `404`.** The id exists; its features are
  unusable. A `404` would tell a client to go hide the film, and it would be
  wrong. `details.recommendable` exists to let a client avoid the request.
- **Startup failure is `503` on data routes while `/health` stays `200`.** A
  supervisor must still be able to see the process. Crashing instead would turn
  a diagnosable "corpus missing at this path" into an opaque boot loop; the
  `/ready` body names the path and the command that fixes it, and contains no
  traceback and no secret.

An unhandled exception returns a fixed `500` body. The detail goes to the server
log only — `test_unexpected_error_is_not_leaked` raises a
`RuntimeError("SECRET-INTERNAL-DETAIL /etc/passwd")` and asserts neither string
reaches the client.

---

## 5. Startup and failure behaviour

The corpus is read and vectorised **once**, in the lifespan handler, via
`run_in_threadpool` so the event loop is not blocked. The resulting
`CatalogueService` and its search index are shared by every request.

The Phase 3 artifact is a tokenised corpus, not a serialised matrix, so
"loading the artifacts" here means vectorising the processed tokens once at
startup. Measured at **0.27 s** for 5,000 films. No request ever rebuilds it:
per-request vectorisation would be both wrong (a different vocabulary) and
ruinous — 0.27 s against a 1.0 ms query, on every call.

A missing, empty or unreadable corpus raises `CatalogueLoadError` with an
actionable message; the app comes up **unready** rather than refusing to start.
`/ready` then returns `503` with the same string that was logged at ERROR, so a
failed deploy is diagnosable from the probe alone.

---

## 6. Configuration

All environment variables, prefix `MOVIEMIND_`:

| Variable | Default | Notes |
|---|---|---|
| `MOVIEMIND_CORPUS_PATH` | `data/processed/movies.jsonl` | read once at startup |
| `MOVIEMIND_DEFAULT_K` | `10` | |
| `MOVIEMIND_MAX_K` | `50` | |
| `MOVIEMIND_DEFAULT_SEARCH_LIMIT` | `10` | |
| `MOVIEMIND_MAX_SEARCH_LIMIT` | `50` | |
| `MOVIEMIND_MAX_QUERY_LENGTH` | `200` | characters |
| `MOVIEMIND_CORS_ORIGINS` | 4 localhost origins | comma-separated, never `*` |

**`.env` is deliberately not read.** The repository's `.env` holds the TMDb
credential used by `scripts/fetch_tmdb_snapshot.py`. The API never contacts TMDb,
so a web process has no use for that secret; wiring `env_file=".env"` would give
every API process the ability to load a credential it cannot benefit from.
`test_creating_settings_never_reads_the_env_file` writes a `.env` containing both
`MOVIEMIND_MAX_K=99` and a fake token, chdirs into it, and asserts neither is
picked up.

`max_k=50` is a real ceiling rather than a formality: the Phase 4 backfill (§9)
ranks the *entire* candidate set, so `k` does not bound the work — but response
size, allocation and client rendering all scale with it. `k=50` is accepted,
`k=51` is `422`.

### CORS

Defaults to `http://localhost:5173`, `http://127.0.0.1:5173`,
`http://localhost:3000`, `http://127.0.0.1:3000` — the Vite and CRA dev servers.
Never a wildcard: an open CORS policy lets any page a user visits read their
results. `allow_credentials=False`, because there are no cookies or tokens, so
credentials would only widen the blast radius of a future origin mistake.
`test_foreign_origin_is_not_granted` checks three foreign origins get no grant.

---

## 7. Security and privacy posture

- **No authentication, deliberately.** The catalogue is a public TMDb snapshot
  and the service is read-only. Auth would be a control against a threat that
  does not exist here, plus a credential to leak.
- **No TMDb token at runtime.** Nothing in the request path reads a credential.
  `test_no_tmdb_credential_is_needed` deletes every TMDb variable from the
  environment and exercises the API; `test_settings_ignore_a_tmdb_token` asserts
  that even when one is present no setting absorbs it.
- **The access log records the route template, never the query string.** A
  search term is user input and a movie id is a per-request identifier; an
  operator debugging a `500` needs the route and the status, not what the user
  typed. `test_access_log_records_the_route_not_the_query` asserts a marker query
  never reaches the log.
- **`X-Request-Duration-Ms`** is set on every response, logged server-side and
  exposed to CORS clients so a slow call can be diagnosed from the browser.
- **No cookies are set** and no per-user state exists anywhere.

> **uvicorn caveat.** The access log above is the application's. Uvicorn's own
> access log is a separate component and *does* log the full request line,
> including query strings. Run with `--no-access-log` if search terms must not
> reach disk, or accept that the server-level log contains them.

---

## 8. Attribution

TMDb's terms require the notice to be displayed **verbatim**, and require the
data to be attributed as coming from TMDb. `/api/v1/meta` serves it so a frontend
can render it without retyping it — a paraphrase would not satisfy the licence:

> This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.

The bracketed placeholder in the source template is resolved to "product".
`test_tmdb_notice_is_verbatim` pins the sentence character for character,
including the comma after "certified", against `docs/phase-01-dataset-strategy.md`
§7.1.

TMDb data is free for **non-commercial** use with attribution; commercial use
requires a separate written agreement, and the snapshot is **not
redistributable**. Not legal advice — see `docs/phase-02-dataset-audit.md`.
TMDb also requires re-validating the snapshot within 6 months; the bundled one is
older than that, which is a dataset limitation carried from Phase 2, not
something this phase changes.

---

## 9. Tests

331 passing: 222 from Phases 1–4, 109 new. `tests/test_api.py` is hermetic — it
opens no socket, reads no `.env`, and needs no TMDb token. The corpus is either a
13-film controlled catalogue built inline or the local artifact behind an
explicit `skipif`.

The controlled catalogue is the important one. The 5,000-film snapshot cannot
produce the states the error contract lives in:

| Fixture | Reachable only on a small corpus |
|---|---|
| id `900` | all-zero feature vector → `422 insufficient_features` |
| 13 films | a valid set smaller than `k=10` → `evidence.exhausted: true` |
| overlapping clusters | genuine backfill → `candidates_rejected > 0` |

None of the three occur naturally at `k=10` on 5,000 well-populated films, so a
suite written only against the real corpus would leave the entire error and
exhaustion contract untested. The fixtures are built with the real
`build_movie_features`, so they exercise real tokenisation and normalisation
rather than a hand-written approximation of it.

Coverage highlights: error envelope shape on six routes; `k` and `limit`
boundaries at 1/50/51; ordering, contiguity, score monotonicity and cosine
range; determinism within an app, across app instances, and across independent
corpus loads; query exclusion; no duplicates; `k=3` as a prefix of `k=5`;
CORS allow/deny across default, configured and foreign origins; the `.env` and
token guarantees; and the backfill evidence block reconciling
`candidates_examined = returned + rejected`.

### Verification

| Check | Result |
|---|---|
| `python -m pytest -q` | 331 passed |
| `python -m ruff check moviemind tests scripts` | All checks passed |
| `python -m mypy moviemind` | no issues in 15 source files |
| Real `uvicorn` smoke test | all six endpoints correct over HTTP |
| Phase 4 artifacts | 5 JSON hashes unchanged |
| Config fingerprint | `af683a71…` unchanged |

Lint and type fixes applied to pre-existing files were mechanical and
behaviour-neutral: `UnicodeNormalizationForm` and cast-order annotations, plus
`Mapping` instead of `dict` in representation signatures. All 222 pre-existing
tests passed before and after.

---

## 10. Limitations

1. **No relevance ground truth, unchanged from Phase 4.** The API is a transport
   over a measured engine; it adds no evidence of its own. `/meta` publishes the
   Phase 4 caveats verbatim so they cannot be lost between the report and a client.
2. **The thin-evidence rate is inherited, not fixed.** At `min_shared_terms=3`,
   49.3% of top-10 results rest on 3 shared terms. A returned film is a
   defensible match, not a verified one, and the HTTP layer cannot change that.
3. **Backfill is O(catalogue) per query.** Fine at 5,000 films (1.0 ms median at
   `k=10`, 1.4 ms at `k=50`); a 100× larger index would need an approximate
   re-rank. `max_k` bounds the response, not the scan.
4. **Search is a linear scan of 5,000 titles** (0.55 ms median), deliberately
   unindexed — an inverted index would add invalidation logic and a second thing
   to keep correct for no measurable gain at this size. The tradeoff inverts
   somewhere past ~10⁵ titles.
5. **No cache, no rate limit, no auth.** Correct for a single-user local tool and
   wrong for a public deployment. Adding them here would be scope the brief
   excluded.
6. **Write support is absent by design.** No endpoints mutate anything, so there
   is no favourites, no feedback loop, and no way for a user's corrections to
   reach the index. That is also why there is nothing to persist.
7. **Snapshot age.** TMDb's 6-month re-validation window has passed. The licence
   position in §8 is a reading of the terms, not a legal opinion.
8. **The access log is not the only log.** See the uvicorn caveat in §7.

---

## 11. Files changed

| File | Status |
|---|---|
| `moviemind/api/__init__.py` | new |
| `moviemind/api/settings.py` | new |
| `moviemind/api/schemas.py` | new |
| `moviemind/api/errors.py` | new |
| `moviemind/api/service.py` | new |
| `moviemind/api/app.py` | new |
| `tests/test_api.py` | new — 109 tests |
| `pyproject.toml` | new — dependencies, ruff, mypy, pytest config |
| `moviemind/config.py` | type annotation only |
| `moviemind/labels.py` | type annotation only |
| `moviemind/recommend.py` | type annotation only |
| `moviemind/representations.py` | `Mapping` instead of `dict` in signatures |
| `scripts/*.py`, `moviemind/experiments.py`, `moviemind/__init__.py` | ruff import-order autofixes |

**Unchanged:** `moviemind/recommend.py` behaviour, all of `data/processed/`, the
selected configuration, and every Phase 1–4 measurement.

---

## 12. Phase 5 checklist

| Requirement | Status |
|---|---|
| Health endpoint | Done — `/health`, 200 independent of data |
| Readiness endpoint | Done — `/ready`, 200 + stats or 503 + actionable reason |
| Movie details | Done — no token/document leakage |
| Recommendations with configurable `k` | Done — default 10, max 50, engine's ordering untouched |
| Title search with bounded results | Done — titles only, deterministic, capped at 50 |
| Stable error format | Done — one envelope, 11 documented codes |
| CORS for local development | Done — explicit 4-origin default, never `*` |
| Uses existing artifacts, does not regenerate | Done — read once, never written |
| No network calls at runtime | Done — no TMDb token read or required |
| No auth / database / frontend | Confirmed — none added |
| Tests | Done — 109 new, hermetic, 331 total |
| Docs | This file |
| Stop after this phase | Confirmed |
