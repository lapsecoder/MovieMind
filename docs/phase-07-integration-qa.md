# Phase 7 — Integration & End-to-End QA

**Status: PASS.** The complete real application flow was exercised against the
running FastAPI backend and the running Vite development server, using the real
5,000-film corpus and the locked Phase 4 engine. No mock backend, no retraining,
no methodology change, no hosting.

This phase is local integration QA only. The later repository-initialisation,
documentation, and browser/accessibility pass is recorded separately in
`docs/phase-08-final-qa.md`; the results below are the Phase 7 results and are
left exactly as measured.

---

## 1. Objective

Verify the whole existing system end to end:

```
Search → select movie → view details → request recommendations → display ranked results
```

The point is confidence in what already exists, not new features. Phase 6 remains
the frontend baseline; this phase changed no production code, only added four
regression tests and this document.

---

## 2. Environment and setup

| Item | Value |
| --- | --- |
| OS / shell | Windows, PowerShell 5.1 |
| Backend | `python -m uvicorn moviemind.api.app:app --port 8000` (repo-documented) |
| Frontend | `npm run dev -- --port 5173` (Vite 7.3.6) |
| Proxy | `/api` → `http://127.0.0.1:8000` via `server.proxy` |
| Node / npm | v24.20.0 / 11.19.0 |
| Corpus | 5,000 films, 23,404 dimensions, 226,352 non-zeros, loaded in 0.31 s |
| Config | representation `D`, `min_shared_terms=3`, `similarity=cosine` |

Browser path used for all journey traffic: `http://localhost:5173` → Vite proxy →
FastAPI. This is the same-origin route the app itself uses; the browser never
learns the backend's address.

---

## 3. Commands executed

Frontend (in `frontend/`):

```
npm run typecheck
npm run lint
npm test
npm run build
```

Backend (in repo root):

```
pytest -q
ruff check moviemind tests scripts
mypy moviemind
```

Servers:

```
python -m uvicorn moviemind.api.app:app --port 8000 --log-level info
npm run dev -- --port 5173
```

---

## 4. Quality-gate results

| Gate | Command | Result |
| --- | --- | --- |
| FE typecheck | `npm run typecheck` | **PASS** — exit 0, no errors |
| FE lint | `npm run lint` | **PASS** — exit 0, 0 errors, 0 warnings |
| FE tests | `npm test` | **PASS** — 2 files, **73 passed** / 73 |
| FE build | `npm run build` | **PASS** — 43 modules, 1.05 s |
| BE tests | `pytest -q` | **PASS** — **331 passed** in 6.53 s |
| BE lint | `ruff check moviemind tests scripts` | **PASS** — All checks passed |
| BE types | `mypy moviemind` | **PASS** — no issues in 15 source files |

Test counts moved from 69 to 73 because of the four regression tests added in
sec 9. Backend count is unchanged at 331.

Build output: `index.html` 0.95 kB, CSS 16.95 kB (4.34 kB gzip), JS 250.23 kB
(77.66 kB gzip). Unchanged from the Phase 6 baseline.

---

## 5. Repository inventory (before changes)

### 5.1 There is no version control repository

> **Historical note.** This section records the repository state *during Phase 7*,
> when `D:\MovieMind` contained no `.git` directory and `git status` returned
> `fatal: not a git repository`. A repository was initialised later, in Phase 8;
> see `docs/phase-08-final-qa.md`. The findings below are left as measured.

`D:\MovieMind` contains **no `.git` directory**. `git status` returns
`fatal: not a git repository`.

This is the single most important environmental finding, because it changes what
several Phase 7 checks can actually mean:

* The root `.gitignore` is present and correct, but **inert** — it documents
  intent and enforces nothing until the project is initialised.
* "Confirm git diff/status", "ensure no secrets are staged", and "raw dataset
  remains untracked" **cannot be verified as stated**. Nothing is staged because
  nothing is tracked.
* **Mitigation applied:** a SHA-256 baseline of all 100 project files (excluding
  `node_modules`, `dist`, and tool caches) was captured before any change and
  re-verified after. This gives byte-level change detection, which is strictly
  stronger than `git diff` for the question actually being asked — *did any
  backend, data, or artifact file change?* No VCS was initialised, because
  creating one is a project-structure decision outside this phase's scope.

### 5.2 Phase 6 baseline intact

All 22 frontend source files, 13 test fixtures, both test suites, and
`docs/phase-06-frontend.md` present and unmodified at inventory time.
`data/processed/movies.jsonl` verified at 5,000 lines.

---

## 6. Real backend startup

Startup log, no warnings, no artifact errors:

```
INFO:     Started server process [33068]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

`GET /api/v1/health`

```json
{"status":"ok","service":"moviemind-api","api_version":"v1"}
```

`GET /api/v1/ready`

```json
{"status":"ready","reason":null,"catalogue":{"films":5000,"dimensions":23404,
 "nonzeros":226352,"density_percent":0.19343,"load_seconds":0.3144}}
```

`GET /api/v1/meta` — returns the locked Phase 4.1 configuration, the attribution
notice, and the five Phase 4 caveats. Fingerprint:

```
af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7
```

**PASS** — processed recommendation artifacts loaded successfully.

---

## 7. Real frontend startup and proxy

```
VITE v7.3.6  ready in 705 ms
Local: http://localhost:5173/
```

`GET http://localhost:5173/` → `200`. Every journey request below was issued
against port 5173, proving the proxy path works end to end.

No TMDb token and no backend secret was placed in any frontend environment
variable. The only env var the frontend reads is the optional
`VITE_API_BASE_URL` (see sec 10).

---

## 8. User-flow results

### 8.1 Search — 8 queries, real corpus

| Query | count / total | Top hit | match |
| --- | --- | --- | --- |
| The Shawshank Redemption | 1/1 | The Shawshank Redemption (1994), id 278 | `exact` |
| Godfather | 2/2 | Bonanno: A Godfather's Story (1999) | `title_contains` |
| Toy Story | 0/0 | — | — |
| Alien | 5/7 | Alien 2: On Earth (1980) | `title_prefix` |
| Amelie | 0/0 | — | — |
| Seven Samurai | 0/0 | — | — |
| The Naked Gun 2 | 1/1 | The Naked Gun 2: The Smell of Fear (1991) | `title_prefix` |
| zzzznotafilmtitle | 0/0 | — | — |

**Response fields matched the schema exactly** — set equality against
`SearchResponse`/`SearchHit` for every query, with `match` always one of the four
`MatchKind` values, and `count` always equal to `len(results)`.

**The zero-result rows are correct, not a defect.** Verified directly against
the corpus: `Toy Story`, `Amelie`, `Seven Samurai`, `Citizen Kane` and
`Casablanca` are **not present** in the 5,000-film subset at all, so returning
nothing is the only truthful answer. Likewise `The Godfather` itself is absent —
only *The Godfather Family: A Look Inside* is in the corpus — so ranking that
first for the query "Godfather" is correct tokenised matching, not a ranking bug.
This is worth knowing as a product limitation: the corpus is a subset, and famous
blockbusters are frequently missing from it.

No request to any TMDb host originated from the browser. See sec 10.

### 8.2 Details

`GET /api/v1/movies/278` — schema exact, 16 fields, no `overview` and no
`tagline`:

```
title='The Shawshank Redemption' year=1994 runtime=142 lang=en
doc_size=75 recommendable=True
feature_counts={cast:10, director:2, genres:2, keywords:22, overview:39}
```

Rendered in the real UI, the panel showed the title, `1994 · en`, genre chips,
`Runtime 2h 22m` (formatted from the live 142 minutes), `Released 1994-09-23`,
`Vote 8.7 / 10 (31,411 votes)`, `Indexed terms 75`, and the real
`feature_counts` breakdown. The intentionally missing overview was communicated
by the dedicated note, confirmed by DOM assertion on `no-overview-note`:

> No plot summary: synopsis text stays in the private raw snapshot and is never
> sent to the browser.

Sparse film `497221` (*Nightclub*, 1983) also rendered correctly with a real
`document_size` of 11.

### 8.3 Recommendations

`k=5` and `k=10` were both exercised for eight films across four query types.
Every one of the 16 matrix cells passed all invariants:

* ranks are sequential `1..n`
* ordering matches the backend array exactly
* no duplicate `movie_id`
* the query film is excluded
* `len(recommendations) <= k`
* `evidence.returned == len(recommendations)`
* `candidates_examined == returned + candidates_rejected`
* every result satisfies `shared_terms >= min_shared_terms`
* scores are raw values in `[0, 1]`
* scores are non-increasing down the list

Representative result, *The Shawshank Redemption* (id 278), k=10:

```
#1  The Green Mile        (1999)  0.28230  6 shared terms
#2  The Majestic          (2001)  0.25853  4 shared terms
#3  The Mist              (2007)  0.24403  4 shared terms
#4  Five Cartridges       (1960)  0.15917  3 shared terms
#5  A Walk Among the Tombstones (2014) 0.14281  5 shared terms
#6  Marie: A True Story   (1985)  0.12086  6 shared terms
#7  The Score             (2001)  0.11764  3 shared terms
#8  Diary of a Mad Housewife (1970) 0.11507 4 shared terms
#9  The Private Navy of Sgt. O'Farrell (1968) 0.10982 4 shared terms
#10 Stir Crazy            (1980)  0.10827  7 shared terms
evidence: 10 of 17 candidates examined met the threshold of 3 shared indexed
terms; 7 rejected.
```

Displayed in the UI as `Similarity 0.28`, never `28%`. A DOM assertion checked
that no recommendation row contains the character `%`. Shared-term counts were
asserted per row against the live payload, and the evidence summary was asserted
to carry the real `10 of 17` denominator and `7 rejected` tally.

The strongest evidence that ordering is not reimplemented client-side: the
server's array order, rank order, and descending-score order are all identical in
every cell, and the rendered DOM sequence matched the server sequence
element-by-element. The frontend also performed no arithmetic on `score` — a
source scan found no cosine, TF-IDF, dot-product or norm computation, and no
`.filter()`/`.slice()` on recommendations. The only `.sort()` in the entire
frontend sorts the derived skip-reason tally, which is counting, not ranking.

### 8.4 Different query types

| Type | Film | Result |
| --- | --- | --- |
| Popular mainstream | Blade Runner 2049 (2017) | 5 results, Ghost in the Shell #3 |
| Popular mainstream | Interstellar (2014) | 5 results, The Prestige #1 at 0.29661 |
| Older | The Good, the Bad and the Ugly (1966) | A Fistful of Dollars #1, 0.39902, 11 shared terms |
| Older | 2001: A Space Odyssey (1968) | 5 results, no exhaustion |
| Older | From Russia with Love (1963) | Thunderball #1 (0.39402), Goldfinger #5, up to 15 shared terms |
| Sparse metadata | Lost Youth (1982), `document_size=9` | 1 result, `exhausted=true`, 972 examined |
| Sparse metadata | Nightclub (1983), `document_size=11` | 0 results, `exhausted=true`, 577 examined |
| Strong overlap | The Good, the Bad and the Ugly (1966) | full western cluster, max 11 shared terms |

The strong-overlap case is the clearest quality signal: the 1963 Bond film
returns Thunderball and Goldfinger, and the 1966 western returns A Fistful of
Dollars and Duck, You Sucker. Shared-term evidence is doing real work.

`k=5` was verified to be an exact prefix of `k=10` for id 278, i.e. widening `k`
widens the window without reordering.

---

## 9. Edge-case results

### 9.1 Against the real backend — 31 of 32 checks as first written

| Case | Expected | Actual | Verdict |
| --- | --- | --- | --- |
| Empty `q` | 400 `empty_query` | as expected | PASS |
| Whitespace-only `q` | 400 `empty_query` | as expected | PASS |
| Missing `q` | 4xx envelope | as expected | PASS |
| No results (`zzzznotafilmtitle`) | 200, count 0 | as expected | PASS |
| No results (`qqqqqqqq`) | 200, count 0 | as expected | PASS |
| Unknown id `999999999` | 404 `unknown_movie` | as expected | PASS |
| id `0` | 404 `unknown_movie` | as expected | PASS |
| Negative id `-5` | 404 `unknown_movie` | as expected | PASS |
| Valid film, zero recs | 200, empty set | as expected | PASS |
| `k=1 / 5 / 10 / 20 / 50` | 200 | as expected | PASS |
| `k=0 / 51 / -1 / 1000` | 422 `invalid_k` | as expected | PASS |
| `limit=1 / 10 / 50` | 200 | as expected | PASS |
| `limit=0 / 51` | 4xx | as expected | PASS |
| 200-char query | 200 | as expected | PASS |
| 201-char query | 4xx | as expected | PASS |
| Unknown route | 4xx `not_found` | as expected | PASS |
| 1-char query `a` | *(expected 4xx)* | **200, 3,616 matches** | **see below** |

**The 1-char query is not a defect.** My first-pass expectation was wrong. The
2-character minimum is a deliberate *client-side* UX guard
(`useMovieSearch.ts:64` refuses to issue the request; `SearchPanel.tsx:258` shows
"Type at least 2 characters."), and `schemas.py` defines no server-side minimum —
only an empty check and `max_query_length=200`. The backend is permissive by
design and the frontend simply never asks. No change was made, and validation was
not weakened.

### 9.2 Genuine coverage gaps found and closed

Two Phase 7 requirements had **no test coverage at all**, which is a QA defect
even though the components themselves were correct:

1. **Loading state** — zero assertions existed for any of `search-skeleton`,
   `recommendations-skeleton`, or `details-skeleton`.
2. **Recommendation request failure** — only the 422 `insufficient_features` case
   was covered. A genuine engine failure was untested.

Four regression tests were added to `frontend/src/test/app.test.tsx`:

* `shows a skeleton while the search request is still in flight`
* `shows a skeleton while recommendations are still in flight`
* `surfaces a server-side recommendation failure with the backend message`
* `reports a dead backend during the recommendation request`

All four passed **without any production code change**, confirming the components
were already correct and the gap was purely in verification. They use the existing
`never: true` and `networkError: true` mock options, so no new infrastructure was
introduced. Frontend tests: 69 → 73.

### 9.3 Already covered by the existing suite

Empty search, no results, unknown movie, dead backend, malformed body, abort,
timeout, k changes, empty recommendation set, reset via Escape, and
recommendation-as-new-query were all already asserted and re-verified in the
73-test run.

---

## 10. API contract check

A mechanical comparison extracted the field set of every Pydantic model in
`moviemind/api/schemas.py` and every TypeScript interface in
`frontend/src/api/types.ts`, then compared them against each other and against
live response keys.

**Result: no schema drift.** 15 models matched field-for-field, including
`MovieDetail` (16/16), `RecommendationItem` (11/11), `EvidenceInfo` (6/6) and
`RecommendationResponse` (6/6). `MatchKind` and `SkipReason` matched value for
value. `Literal` pins for `status` matched on both sides. Every live response
key set matched the TypeScript type exactly — no server-only fields the client is
unaware of, and no client-expected fields the server omits.

A leak check confirmed no response contains `document`, `tokens`, or
`stinger_keywords_removed`.

The live integration harness also drove the real `<App />` with real captured
responses and asserted rendered title, year, formatted runtime, `feature_counts`,
`no-overview-note`, exact recommendation order, per-row `Similarity 0.28` text,
per-row shared-term counts, the `10 of 17 … 7 rejected` evidence line, live
attribution text, and both `k` widths — 7 of 7 passed. That harness was temporary
and has been removed, so `npm test` stays hermetic and does not require a running
backend.

**No production change was required. No defect was found in the contract.**

---

## 11. Security and privacy checks

| Check | Result |
| --- | --- |
| TMDb token read only where the backend needs it | **PASS** — read only by `scripts/fetch_tmdb_snapshot.py` (the offline Phase 1 fetcher). The API server never reads it. |
| Token absent from every frontend file | **PASS** — searched all of `frontend/` (src, dist, configs) for the full 239-char token: zero hits |
| No TMDb domain in frontend source | **PASS** — every "TMDb" occurrence is a comment, tooltip, or the required attribution text. No `api.themoviedb.org`, no image CDN. |
| Frontend calls go to our backend only | **PASS** — all traffic is through the single centralised client; built output contains `/api/v1` and zero hard-coded hosts |
| `.env` ignored | **PASS** — root and `frontend/.gitignore` both exclude `.env`; **caveat: inert without git** (sec 5.1) |
| Raw dataset untracked/ignored | **PASS by intent** — root `.gitignore` excludes `data/*`, `data/raw/`, `data/processed/`; **not verifiable as "untracked" without git** |
| No PII or secrets logged | **PASS** — no `console.*` in frontend runtime source; no user data exists to log (no accounts, no persistence) |
| No recommendation data persisted by the frontend | **PASS** — no `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie`, `caches.*` or `sendBeacon` anywhere in frontend source |

The application collects nothing, stores nothing, and has no accounts. The only
personal-data surface in the project is the local `.env` file, which holds a
credential and is excluded by ignore rules.

---

## 12. Production build check

`npm run build` succeeds. `dist/` inspected for accidental secret exposure:

* No TMDb token, no JWT, no `Bearer` credential, no `TMDB_API_*` variable
* **0** occurrences of `VITE_` — no env value inlined
* **0** hard-coded `localhost:8000` or `127.0.0.1:8000`
* `/api/v1` present once, as a relative prefix
* The only absolute URLs in the bundle are XML namespace URIs
  (`www.w3.org/...`), a `http://placeholder` literal, and React's error-decoder
  link. No CDN, no font host, no analytics, no external request at runtime.

`dist/` is 3 files, ~268 kB total. Not deployed, and no hosting provider was
introduced.

---

## 13. Regression and invariant checks

| Invariant | Verification | Result |
| --- | --- | --- |
| Config fingerprint `af683a71…` | Full value read from the repo (`phase-04-recommendation-evaluation.md:19,458`; `phase-05-api.md:23`) and compared to live `/meta` | **PASS** — exact match: `af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7` |
| Selected config unchanged | `representation=D`, `min_shared_terms=3`, `similarity=cosine` | **PASS** |
| Corpus is exactly 5,000 films | `/ready` reports 5,000; `movies.jsonl` has 5,000 lines | **PASS** |
| Corpus byte-identical to Phase 4 pin | `experiment_manifest.json` `corpus_sha256` vs recomputed SHA-256 | **PASS** — `53b0b8c675651ea438230fe75bba3660f52213428876f9cb99e565825fcdece0` |
| Recommendation artifacts unchanged | SHA-256 of all 13 `data/` files vs pre-change baseline | **PASS** — all identical |
| Phase 4 evaluation artifacts unchanged | `experiments.json`, `evaluation_labels.json`, `experiment_manifest.json`, `qualitative_check.json`, `build_manifest.json` | **PASS** — all identical; 33-experiment grid intact |
| Phase 4/4.1 methodology untouched | No file under `moviemind/`, `data/`, `tests/`, `scripts/` modified | **PASS** — 54 files byte-identical |
| Backend recommendation tests pass | `pytest -q` | **PASS** — 331 passed |
| Frontend has no own recommendation logic | Source scan for `.sort()`, score arithmetic, cosine/TF-IDF, filtering | **PASS** — only the skip-reason tally is sorted; no scoring, no filtering, no reordering |

**Note on the two fingerprints.** `data/processed/build_manifest.json` carries
`4615a843…`, which is the Phase 3 *feature/preprocessing* config, and
`experiment_manifest.json` carries per-experiment fingerprints for the 33-cell
Phase 4 grid. The Phase 4.1 *selected recommendation* config is `af683a71…`, and
that is the value `/meta` serves. These are three different configs and are
expected to differ; both were left untouched.

---

## 14. Defects discovered and fixes applied

**No production defects were found.** The backend, the recommendation engine, the
artifacts, and the Phase 6 frontend all behaved correctly against real data.

One QA defect was found and fixed:

| # | Defect | Fix | Files changed |
| --- | --- | --- | --- |
| 1 | No test coverage for the loading state, and none for a genuine recommendation-request failure (only the 422 case existed) | Added 4 regression tests using existing mock options; all passed with no production change | `frontend/src/test/app.test.tsx` |

Three items I flagged during the phase were investigated and confirmed **not** to
be defects, with no change made:

* 1-character search returning 3,616 matches — deliberate client-side guard.
* `Toy Story` / `Amelie` / `Seven Samurai` returning no results — those films are
  absent from the 5,000-film corpus subset.
* Two distinct fingerprints (`4615a843…` vs `af683a71…`) — different configs.

---

## 15. Files changed

| File | Change |
| --- | --- |
| `frontend/src/test/app.test.tsx` | +4 regression tests (loading skeletons, rec 500 failure, rec network failure) |
| `docs/phase-07-integration-qa.md` | This document (new) |

Unchanged and verified byte-identical: all 32 files under `moviemind/`, `tests/`
and `scripts/`; all 13 files under `data/`; `pyproject.toml`; `.env.example`; root
`.gitignore`; and all 5 existing frontend source files, 4 existing test files, 13
fixtures and 4 build configs. `frontend/dist/` is build output and is ignored.

No secrets, no raw dataset, and no unrelated files were added or modified.
No VCS exists, so "staged" is not applicable (sec 5.1).

---

## 16. Known limitations

1. **No version control.** The strongest available substitute (SHA-256 baseline
   of 100 files) was used, but the ignore rules currently protect nothing. This
   should be addressed before the project is shared or published.
   *Resolved in Phase 8:* `git init` was run, the ignore rules were verified
   against the real index, and every staged path was audited. See
   `docs/phase-08-final-qa.md`.
2. **No real-browser verification.** No headless browser is available in this
   environment. Rendering was verified in jsdom against real backend payloads,
   which exercises the components and the real contract, but not physical layout,
   actual font rendering, or a real screen reader.
   *Resolved in Phase 8:* Microsoft Edge was driven over CDP for layout, focus,
   pointer-target, and network-origin checks. Full text rendering and a physical
   screen reader remain untested.
3. **The 5,000-film corpus is a subset.** Many famous films are absent and return
   no results. This is correct behaviour, but it is the most likely source of
   user confusion in the product.
4. **Zero-result films are common by design.** With `min_shared_terms=3`, a
   measurable thin-evidence rate applies. The UI explains this rather than hiding
   it, but the empty state will be seen regularly.
5. **Absolute `VITE_API_BASE_URL` path unexercised.** The same-origin default was
   verified live end to end; the cross-origin branch is unit-tested only.
6. **`eslint@9.39.5` is deprecated**, and `npm audit` reports 2 moderate
   vulnerabilities in the dependency tree. Neither affects runtime behaviour, and
   neither was touched in this phase.
7. **Split-origin deployment not tested.** Same-origin via the Vite proxy is the
   supported local configuration; a separate static host plus API host was out of
   scope.

---

## 17. Final status

| Gate | Status |
| --- | --- |
| Repository inventory and Phase 6 baseline | **PASS** (with the no-VCS caveat in 5.1) |
| Frontend typecheck / lint / test / build | **PASS** |
| Backend pytest / ruff / mypy | **PASS** |
| Real backend startup, artifacts, `/health`, `/meta` | **PASS** |
| Real frontend startup and proxy to backend | **PASS** |
| A. Search | **PASS** |
| B. Selection and details | **PASS** |
| C. Recommendations (k=5, k=10) | **PASS** |
| D. Diverse query types | **PASS** |
| E. Edge cases and error states | **PASS** |
| API contract / schema drift | **PASS** — no drift |
| Security and privacy | **PASS** |
| Production build inspection | **PASS** |
| Phase 4/4.1 regression invariants | **PASS** |
| Documentation | **PASS** |

**Phase 7 is complete.**

**Phase 8 has NOT started.** No deployment, hosting, accounts, database, cloud
service, or payment infrastructure was introduced. The project remains ₹0 and
local-first. The recommendation methodology, the locked Phase 4.1 configuration,
and the ML artifacts are untouched.
