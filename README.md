# MovieMind

Content-based movie recommendations from a local TMDb snapshot. Type a film
title, read its metadata, and see the other films the engine considers similar
— with the evidence behind every claim on screen.

No trained model, no database, no accounts, no cloud services, no per-user data.
Similarity is computed at request time over a sparse TF-IDF matrix of 5,000
films that is built once at startup.

---

## What it does

* **Search by title** over the 5,000-film snapshot.
* **Read details** for the selected film: year, runtime, genres, feature counts.
* **Get recommendations** ranked by cosine similarity, with the shared terms that
  justify each result and a tally of candidates that were examined but rejected.
* **See the honest numbers.** Similarity is a raw score, never a percentage.
  Returned-versus-examined counts are always visible. The TMDb attribution
  notice is always on screen, even when the API is unreachable.

The frontend renders the backend's ranking order exactly as received. It does not
re-sort, re-score, filter, or reformat results.

---

## How the recommendation works

The engine is fixed-configuration retrieval, chosen by measuring four
representations and thirty-one ablations end to end (see
`docs/phase-04-recommendation-evaluation.md`).

| | |
| --- | --- |
| Representation | **D** — per-field TF-IDF blocks, L2-normalised, stacked with weights |
| Fields | overview, genres, keywords, cast, director |
| Similarity | cosine (dot product over L2-normalised vectors) |
| Genre weight | 0.44 |
| Evidence floor | `min_shared_terms = 3` |
| Catalogue | 5,000 films, 23,404 dimensions, 226,352 non-zeros |
| Ties | broken by ascending TMDb id, so results are bit-reproducible |

Two constants decide the output, and they are defined once in
`moviemind/experiments.py` (`SELECTED_GENRE_WEIGHT`, `SELECTED_MIN_SHARED_TERMS`).
The API imports them rather than restating them, so a literal cannot drift from
the measured configuration.

The configuration fingerprint is
`af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7`. The
`/api/v1/meta` endpoint serves it, and the About panel displays its first twelve
characters, so a running server can always be checked against the reported one.

### Why the evidence floor exists

`min_shared_terms = 3` leaves a measured thin-evidence rate of 49.3% at the top
10. That is a deliberate trade: fewer results, but each one has at least three
shared terms behind it. A result shorter than `k` means the catalogue held too few
candidates above the evidence floor — not that the engine stopped searching.

---

## Project layout

```
moviemind/            Python package: text, features, retrieval, evaluation
  text.py             tokenisation, stopwords, English function words
  representations.py  Phase 3 feature blocks (representations A-D)
  recommend.py        the engine: ranking, evidence, filtering
  experiments.py      Phase 4 experiment grid and the selected configuration
  evaluate.py         metric definitions
  labels.py           structural proxy labels for evaluation
  pipeline.py         snapshot -> features -> processed artefacts
  api/                FastAPI surface (app, schemas, service, errors, settings)
frontend/             React 19 + TypeScript + Vite + Tailwind v4 client
  src/api/            typed client; every network call is built here
  src/components/     SearchPanel, MovieDetails, Recommendations, AboutPanel
  src/hooks/          per-endpoint data hooks with abort handling
  src/test/           Vitest + Testing Library, fixtures from real payloads
scripts/              dataset fetch, feature build, experiment run, audits
tests/                pytest suite (hermetic unit + real-catalogue integration)
data/
  raw/                TMDb snapshot (gitignored)
  processed/          built features and matrices (gitignored)
docs/                 one report per phase, this README's companion notes
```

`docs/architecture.md` describes how a request travels through these layers.

---

## Setup

Requires Python 3.11+ and Node 20+.

### 1. Backend

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows; `source .venv/bin/activate` elsewhere
pip install "numpy>=1.26" "scipy>=1.11" "scikit-learn>=1.3" \
            "fastapi>=0.110" "uvicorn[standard]>=0.27" \
            "pydantic>=2.6" "pydantic-settings>=2.2"
```

For the test and lint tools:

```bash
pip install "pytest>=8.0" "httpx2>=0.1" "ruff>=0.5" "mypy>=1.9"
```

`pyproject.toml` declares these dependencies but deliberately has **no build
backend**, so `pip install -e .` does not work and is not meant to. MovieMind is
run from the repository root, not installed as a package. The declared versions
are the floor; the versions this was verified against are listed in
`docs/phase-08-final-qa.md`.

### 2. Dataset

The processed corpus is gitignored, so it is not in a fresh clone. Two options:

```bash
# Option A: fetch a fresh snapshot (needs a free TMDb token, see .env.example)
python scripts/fetch_tmdb_snapshot.py

# Option B: bring your own data/raw/*.json.gz export and audit it first
python scripts/audit_dataset.py

# Then build the features
python scripts/build_features.py
```

The default corpus path is `data/processed/movies.jsonl`. Expected fingerprint of
the current build: SHA-256
`53b0b8c675651ea438230fe75bba3660f52213428876f9cb99e565825fcdece0`, 5,000 lines.

### 3. Frontend

```bash
cd frontend
npm install
```

### 4. Run

```bash
# terminal 1 — API on :8000
python -m uvicorn moviemind.api.app:app --port 8000

# terminal 2 — client on :5173, proxying /api to the backend
cd frontend && npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to
`http://127.0.0.1:8000`, so the browser only ever talks to one origin. There is
no `VITE_API_URL` to set and no CORS configuration to get wrong.

Startup loads and vectorises the 5,000-film corpus in about 0.27 s. API docs are
at http://localhost:8000/docs.

---

## Tests and checks

```bash
pytest -q                                  # 331 tests
ruff check moviemind tests scripts
mypy moviemind

cd frontend
npm test                                   # 77 tests
npm run typecheck
npm run lint
npm run build
```

The frontend test fixtures are real payloads captured from a running Phase 5
backend, not hand-written shapes, so the tests exercise the actual contract. The
backend suite is hermetic except for `tests/test_snapshot_integration.py`, which
skips when no snapshot is present.

---

## Documentation

| Document | Contents |
| --- | --- |
| `docs/architecture.md` | Layers, data flow, request lifecycle |
| `docs/phase-01-dataset-strategy.md` | Snapshot choice and collection approach |
| `docs/phase-02-dataset-audit.md` | Data quality, licensing, attribution requirements |
| `docs/phase-03-feature-engineering.md` | Representations A-D, feature construction |
| `docs/phase-04-recommendation-evaluation.md` | The experiment grid, metrics, selected configuration |
| `docs/phase-05-api.md` | The six endpoints and their contract |
| `docs/phase-06-frontend.md` | Frontend architecture and accessibility decisions |
| `docs/phase-07-integration-qa.md` | Real end-to-end integration run |
| `docs/phase-08-final-qa.md` | Final QA, browser/accessibility audit, repository state |

---

## Known limitations

1. **The catalogue is 5,000 films, not all of TMDb.** Many well-known films are
   absent and return no results. This is the most likely source of "why does it
   have nothing for X?".
2. **There is no relevance ground truth.** Every Phase 4 score is agreement with
   a structural proxy label, not with human judgement or user satisfaction. The
   evaluation measures internal consistency, not quality in the way a viewer
   would define it.
3. **49.3% of top-10 results have thin evidence.** The `min_shared_terms = 3`
   floor leaves roughly half of returned films with the minimum support. Each one
   is a defensible match on the text available, not a verified one.
4. **Cast-based search is poorly served.** Cast is diluted inside representation D
   (recall@10 of 0.157), so a user searching for a film by an actor's name will
   often not find it. Search deliberately covers titles only.
5. **No user-facing filtering or sorting.** Results are shown in the engine's
   order. There are no genre filters, no date range, no "similar to this but
   more recent".
6. **Not a real-time system.** The corpus is a static snapshot and there is no
   incremental update path. Rebuilding features is a batch operation.
7. **No authentication, no database, no persistence.** Every request is
   stateless. Nothing is stored about the user, because nothing can be.
8. **Untested on physical assistive hardware.** The interface was verified
   programmatically in a real browser (focus rings, tab order, pointer target
   sizes, accessible names, live regions), but not with an actual screen reader
   or switch device.
9. **The TMDb snapshot is not redistributable.** TMDb data is free for
   non-commercial use with mandatory attribution; commercial use requires a
   separate written agreement. Not legal advice — see
   `docs/phase-02-dataset-audit.md`.

---

## Data and licensing

This product uses TMDB and the TMDB APIs but is not endorsed, certified, or
otherwise approved by TMDB.

The notice is served by `/api/v1/meta`, rendered by the frontend, and has a
built-in fallback used when the API is unreachable — so attribution is on screen
even in the worst case. The v4 API Read Access Token is only needed to fetch a
snapshot via `scripts/fetch_tmdb_snapshot.py`. The API itself never reads a TMDb
credential and makes no outbound network calls.

---

## Security notes

* `.env` is gitignored and must stay that way. `.env.example` documents the
  variable name with an empty value and is safe to commit.
* The TMDb token is read only by the snapshot fetch script.
* The corpus under `data/` is gitignored; no processed artefact is committed.
* The API is read-only: six `GET` endpoints, no writes, no database.
* The browser calls only its own origin through the dev proxy. It never calls
  TMDb, and it never calls the backend port directly.
* `frontend/dist/` is gitignored build output and was scanned for secrets in
  Phase 8.
