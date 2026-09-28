# Architecture

How a request travels from typing a film title to a ranked list, and what each
layer is responsible for. Phase reports record *what was measured*; this document
records *how the pieces fit*.

---

## 1. The shape of the system

```
                    ┌──────────────────────────────────────────┐
  browser           │  frontend/  (React 19 + TS + Vite)       │
  (single origin)   │                                          │
      │             │  SearchPanel → MovieDetails               │
      │  /api/v1/*  │           → Recommendations → AboutPanel │
      │             │                                          │
      ▼             └──────────────────────────────────────────┘
      │  Vite dev-server proxy (dev only)
      ▼
┌──────────────────────────────────────────────────────────────────┐
│  moviemind/api/          FastAPI, 6 × GET, read-only            │
│    app.py      routes, lifecycle, error envelopes, access log   │
│    service.py  the only caller of the engine; no FastAPI import │
│    schemas.py  Pydantic request/response models                 │
│    errors.py   domain errors → HTTP status                      │
│    settings.py corpus path and startup knobs, all defaulted      │
└──────────────────────────────────────────────────────────────────┘
      │  Recommender.recommend(...)
      ▼
┌──────────────────────────────────────────────────────────────────┐
│  moviemind/recommend.py    ranking, evidence, filtering          │
│    sparse_top_k, ranked_candidates, _positive_scores            │
└──────────────────────────────────────────────────────────────────┘
      │  reads precomputed matrices only
      ▼
┌──────────────────────────────────────────────────────────────────┐
│  data/processed/movies.jsonl + sparse matrices (gitignored)     │
│    5,000 films · 23,404 dimensions · 226,352 non-zeros          │
└──────────────────────────────────────────────────────────────────┘
```

The four backend phases below the API are the frozen, measured engine from
Phases 1–4:

| Module | Responsibility |
| --- | --- |
| `text.py` | tokenisation, stopwords, English function words, unicode folding |
| `representations.py` | Phase 3 feature blocks; representations A–D; L2 normalisation |
| `recommend.py` | sparse top-k, ranking, the evidence and filtering contract |
| `experiments.py` | the Phase 4 experiment grid and the two selected constants |
| `evaluate.py` | metric definitions used by the evaluation report |
| `labels.py` | structural proxy labels |
| `pipeline.py` | snapshot → features → processed artefacts (batch, not served) |

---

## 2. The separation that matters

Three boundaries are structural rather than conventional. Each one exists to
keep a specific class of bug from being possible.

**The engine knows nothing about HTTP.** `moviemind/recommend.py` has no FastAPI
import. The engine was complete, measured, and tested in Phase 4, before an API
existed, and it is still testable without one. The Phase 2 test suite runs
unchanged.

**The service layer is the only caller.** No route in `app.py` reaches into the
engine directly. `service.py` translates HTTP onto engine calls that already
existed, and it imports nothing from FastAPI. That is what lets the same engine
tests run with no app.

**The frontend never computes a recommendation.** Every network call is built in
`frontend/src/api/client.ts`, and the result order is rendered as received. There
is no client-side scoring, sorting, filtering, thresholding, or percentage
conversion. A test asserts that the DOM order matches the backend order exactly.

### The configuration has one source

`SELECTED_GENRE_WEIGHT = 0.44` and `SELECTED_MIN_SHARED_TERMS = 3` live in
`moviemind/experiments.py`, alongside the fingerprint of the configuration they
describe. The service imports them; it does not restate them. A literal `0.44`
typed into the service would be a second source of truth that could drift from
the measured one with nothing failing to warn you.

The fingerprint
`af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7` covers the
representation, field set, weights, geometry, and preprocessing. `/api/v1/meta`
serves it and the About panel shows its first twelve characters, so a running
server can be compared against the reported one at a glance.

---

## 3. A recommendation request, end to end

`GET /api/v1/movies/{id}/recommendations?k=10`

1. **Route** (`app.py`) validates `k` against the limits from settings, rejects
   anything out of range with a 422, and resolves the service from request state.
2. **Service** looks the film up by TMDb id. An unknown id raises
   `UnknownMovieError`; a film too sparse to vectorise raises
   `InsufficientFeaturesError`. Neither reaches the client as a stack trace.
3. **Engine** computes the query vector from the stored feature blocks, takes a
   sparse top-k against the whole catalogue, drops the query film from its own
   results, keeps only positive scores, and applies the `min_shared_terms` floor.
4. **Ranking** sorts by score descending with ties broken by ascending TMDb id, so
   the order is bit-reproducible. The engine returns a `RecommendationResult`
   carrying the ranked list, the examined-versus-returned counts, the shared-term
   evidence per row, and the tally of rejected candidates by reason.
5. **Schema** turns that into the response model. The frontend mirrors these
   types in `frontend/src/api/types.ts`; Phase 7 verified there is no drift
   between the Pydantic models, the TypeScript types, and the live payloads.
6. **Frontend** renders the rows in the received order, each with its raw
   similarity score, the shared terms, and an expandable evidence breakdown.

Latency at `k=10` is about 1 ms engine-side, 6 ms end to end over HTTP. The
dominant cost is startup: loading and vectorising 5,000 films takes roughly
0.27 s, once, in the lifespan handler.

---

## 4. Why the browser only ever sees one origin

In development the Vite server proxies `/api` to `http://127.0.0.1:8000`. The
frontend's client module builds every URL as a same-origin `/api/v1/...` path
relative to the page.

This is a deliberate choice over a `VITE_API_URL` variable plus CORS
configuration. It removes an entire class of failure — a misconfigured base URL
or a missing CORS header is a class of bug that cannot happen if the browser
never makes a cross-origin request. It also means the TMDb attribution notice and
the data come from the same origin, and a test asserts that no browser request
ever targets the backend port directly.

In production the same relative paths work behind any reverse proxy that routes
`/api` to the backend, with no rebuild.

---

## 5. State, and the absence of it

There is no database, no session, and no persistence layer. The only state in the
system is:

* the in-memory service object, built once at startup and shared read-only;
* transient React state in the browser (the current query, the selected film, the
  selected `k`).

Nothing is written at request time. The corpus is opened read-only. There are no
accounts because there is nothing to authenticate and no user data to protect —
which is also why there is no privacy policy to get wrong.

Every endpoint is `GET`. There is no write path to audit.

### Abort and staleness handling

Each endpoint has a dedicated hook in `frontend/src/hooks/`. They debounce the
search input, carry an `AbortSignal` so a superseded request is cancelled rather
than racing, and ignore a response whose signal was already aborted. The
`client.ts` tests assert the relative URL form directly, so a regression that
introduced an absolute base URL would fail rather than quietly depend on dev
proxy configuration.

---

## 6. Error handling

`moviemind/api/errors.py` maps domain errors to status codes, and
`app.py` registers handlers that render a consistent JSON envelope for domain
errors, request-validation errors, HTTP errors, and anything unhandled. The
frontend reads that envelope through `src/api/errors.ts` and surfaces the
backend's own message to the user.

Two cases are deliberately user-facing rather than silent:

* a film with too few features to vectorise returns 422 and is explained in the
  details panel;
* a result set shorter than `k` is stated as a consequence of the evidence floor,
  never padded to `k` and never presented as "search stopped early".

The backend access log is a middleware rather than the default logger, so it
stays quiet during tests.

---

## 7. Data flow at build time

The serving path above reads precomputed artefacts. Producing them is a separate
batch path that never runs while the API is up:

```
data/raw/  (TMDb snapshot, gitignored)
    │  scripts/fetch_tmdb_snapshot.py     ← the only code that reads a TMDb token
    ▼
data/raw/*.json.gz
    │  scripts/audit_dataset.py           ← quality, licensing, attribution
    │  scripts/build_features.py
    │  scripts/build_evaluation_labels.py
    ▼
data/processed/  (gitignored)
    movies.jsonl · matrices · experiment_manifest.json
    │  scripts/run_experiments.py
    │  scripts/compare_representations.py
    │  scripts/qualitative_check.py
    ▼
docs/phase-04-recommendation-evaluation.md   ← where 0.44 and 3 came from
```

The manifest records the corpus SHA-256, the protocol, and every experiment in
the grid. Phase 8 re-verified the corpus at 5,000 lines with SHA-256
`53b0b8c675651ea438230fe75bba3660f52213428876f9cb99e565825fcdece0`, matching the
committed report.

Ties are broken by ascending TMDb id rather than by any non-deterministic
ordering, so the whole pipeline is bit-reproducible from the same snapshot.

---

## 8. Testing strategy

| Layer | Approach | Count |
| --- | --- | --- |
| `moviemind/` | hermetic unit tests, hand-built fixtures | 331 (with `tests/`) |
| `tests/test_api.py` | FastAPI `TestClient` against the real catalogue | included above |
| `tests/test_snapshot_integration.py` | real snapshot, skipped when absent | included above |
| `frontend/src/test/client.test.ts` | URL construction, encoding, abort behaviour | 32 |
| `frontend/src/test/app.test.tsx` | components, keyboard access, error states | 45 |

The frontend fixtures are real payloads captured from a running Phase 5 backend,
not hand-written shapes, so the tests exercise the actual wire contract. A
stronger check runs in Phase 7 and Phase 8: the real backend and the real
frontend are started together and driven through the whole flow, so drift
between the contract and the components fails loudly rather than only in
production.

Phase 8 additionally drove Microsoft Edge over CDP to check what jsdom cannot:
element geometry at three viewport widths, the `:focus-visible` ring on real
keyboard tab stops, pointer-target sizes, accessible names, heading order, and
the set of network origins the browser actually contacts. That audit is what
found the two small defects recorded in `docs/phase-08-final-qa.md`.

---

## 9. Deliberate absences

These are not gaps to be filled later; they are consequences of the design.

* **No trained model.** Nothing is fit to labels, so there is no model artifact,
  no training step, and no drift. Retrieval is fixed configuration plus a matrix
  product.
* **No relevance ground truth.** The evaluation scores agreement with structural
  proxy labels. It measures internal consistency, not human satisfaction, and no
  metric in the report should be read as a quality claim.
* **No database.** The corpus is immutable input. Adding one would imply
  mutability that the pipeline does not have.
* **No authentication.** Nothing is private, so there is nothing to protect.
* **No client-side recommendation logic.** Any re-ranking in the browser would
  make the displayed result different from the measured engine, and the
  evaluation would no longer describe what a user sees.
