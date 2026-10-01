# Deployment

MovieMind runs in production as a **Vercel project**: a static frontend served
from Vercel's CDN and a single Python (FastAPI) Vercel Function behind it, both
on one origin. The corpus is a **build-time input**, not a repository file and
not a static asset.

Nothing about the product changed to make this work. The recommendation
methodology, the API contract, the response schemas and the error envelope are
byte-identical to v1; the deployment added configuration and two build-time
scripts, and no application code.

---

## 1. Architecture

```
                    https://moviemind.example.com
                                 |
                    +------------v-------------+
                    |      Vercel  CDN        |
                    |  public/       (270 kB) |   index.html, hashed assets
                    |  no data/, no .jsonl     |
                    +------------+-------------+
                                 |  anything not in the CDN
                    +------------v-------------+
                    |  Vercel Function (Python)|
                    |  moviemind/api/app.py    |
                    |  -> moviemind.api.app:app|
                    +------------+-------------+
                                 |  read-only, at startup
                    +------------v-------------+
                    |  data/processed/         |
                    |    movies.jsonl (8.8 MB) |   inside the function bundle
                    +--------------------------+   never routed, never served
```

One origin, so the browser makes same-origin requests to `/api/v1/*`. No CORS
preflight in production, no `VITE_API_BASE_URL`, and no reason for the frontend
to know a second hostname. `docs/architecture.md` §4 argued for this shape when
the dev proxy was the only thing providing it; the deployment keeps it.

**Vercel capabilities this relies on**, verified against the documentation on
2026-09-30:

| Requirement | How Vercel satisfies it |
| --- | --- |
| FastAPI backend | Python framework preset; `tool.vercel.entrypoint` in `pyproject.toml` points at the app that already exists, so no new Python file |
| Python dependencies | Read from `[project.dependencies]` in `pyproject.toml`. No `requirements.txt` was added, so there is no second dependency list to drift |
| Vite frontend | Built by the build command, then `deploy/stage_frontend.py` copies `frontend/dist` to `public/`, which the preset serves from the CDN. **`outputDirectory` is not used** — see §6 |
| Function bundle size | The Python builder **enforces 225 MB**, not the 500 MB the function-limits page implies. It does not tree-shake by import: the generated `filePathMap` carried the whole resolved virtualenv, plus a 56 MB `_uv/uv` binary. Measured after three fixes: **183.8 MB**, so `VERCEL_SUPPORT_LARGE_FUNCTIONS` is **not required**. Uncompressed. See §6 |
| Memory | 1024 MB provisioned. Measured resident set after loading all 5,000 films: **80 MB** |
| Duration | 60 s configured; startup measured at **0.41 s** |
| Environment variables | Project settings, encrypted at rest, available to the build and the function, never to the browser |
| Private build inputs | The corpus is downloaded by a build command from a URL the public cannot reach |
| Static file exposure | Only `public/` is served. The function bundle is not an HTTP file server |

**Deliberately not used: Vercel Services.** It is the documented way to run a
polyglot monorepo as one project and would make the routing more explicit, but
it is still labelled Beta with a permissions requirement, and a beta routing
model is not something to bet a deployment on. The single-project shape below
is the stable equivalent.

---

## 2. How `movies.jsonl` reaches the deployment

`data/processed/movies.jsonl` is gitignored and must stay that way. The API
ToU grant TMDb content for non-commercial use with attribution; they do not
grant redistribution, so a corpus in a public repository is a licence problem
before it is a size problem. (See `docs/phase-02-dataset-audit.md` §4 and §15.)

So the corpus travels out of band:

1. Put `movies.jsonl` somewhere the public cannot read. The cheapest option
   that adds no vendor is a **second, private GitHub repository**; any
   authenticated HTTPS file host works equally well, including an object store
   on a free tier. It does not have to be GitHub.
2. Set two **encrypted, non-public** Vercel environment variables (see §4).
3. `deploy/fetch_corpus.py` runs as the first step of the build command. It
   streams the artifact to `data/processed/movies.jsonl`, validates it, and
   exits non-zero on any problem.

`data/processed/movies.jsonl` is already the default of `Settings.corpus_path`,
so **the deployed function needs no corpus-path variable and no application code
changed.** If the corpus is missing, the app comes up *unready* — `/ready`
returns 503 with the reason, `/health` stays 200 — which is the Phase 5 failure
behaviour, unchanged.

The script has three modes and is the same file locally and in the cloud:

| Situation | Behaviour |
| --- | --- |
| `MOVIEMIND_CORPUS_URL` set | Download, validate, optional checksum, install atomically |
| URL unset, corpus already present | Validate it in place — this is what makes `vercel build` and `vercel dev` work on a laptop with no configuration |
| URL unset, no corpus | Exit 1 with the command to run |

It writes to a `.partial` file and `os.replace`s it into position, so a reader
can never observe a half-written corpus, and a failed download leaves nothing
behind. It requires a first and last record to carry `id`, `title` and
`tokens` — `tokens` is what distinguishes a Phase 3 corpus from the raw
snapshot, so a mistyped path pointing at `data/raw/` fails the build instead of
producing a function that starts and then 503s.

Set `MOVIEMIND_CORPUS_SHA256` in production. The script refuses to build if the
artifact and the recorded hash disagree, which catches a truncated upload or a
replaced file before a user sees an empty catalogue.

---

## 3. Why the corpus is not downloadable

Four independent reasons, any one of which is sufficient:

1. **It is not a static file.** Vercel serves exactly one directory to the
   public for a backend preset: `public/`, which holds `index.html`, one JS
   chunk and one CSS file — 270 kB total. The corpus is 8.8 MB and lives in the
   function bundle. Vercel Functions are not file servers; a bundle is readable
   by the function's own code, not by HTTP.
2. **The app has no filesystem route.** `deploy/audit_build.py` asserts that the
   ASGI app contains no `starlette.routing.Mount` and no route accepting
   anything but `GET`/`HEAD`. There is no `StaticFiles`, no `app.mount`, no
   `FileResponse`, no catch-all. The registered paths are the six documented
   endpoints plus FastAPI's generated `/docs`, `/redoc` and `/openapi.json`.
   Verified against the running server: seven path-traversal and
   direct-file probes all return the standard 404 envelope.
3. **The upstream data is not bundled either.** `data/raw/` — the actual TMDb
   snapshot — is excluded from the deployment by `.vercelignore`, by
   `functions[...].excludeFiles`, and by the fact that it is gitignored. It
   exists only on the machine that fetched it.
4. **The responses that are public do not contain corpus data.** `/api/v1/meta`
   publishes the config fingerprint and aggregate geometry. `/api/v1/movies/{id}`
   returns metadata and `feature_counts`, never `tokens` or `document`. That was
   already true and is covered by `tests/test_api.py::TestDetails`.

---

## 4. Environment variables

All are set in Vercel project settings, marked **encrypted** and **not** exposed
to the client. `Settings` never reads `.env`, so a web process cannot load a
credential it has no use for; these are ordinary environment variables.

| Variable | Required | Purpose |
| --- | --- | --- |
| `MOVIEMIND_CORPUS_URL` | yes, in the cloud | Non-public artifact URL for `movies.jsonl`. Optional locally |
| `MOVIEMIND_CORPUS_TOKEN` | if the host needs auth | Sent as `Authorization: Bearer`. Use a fine-grained, read-only, single-repository token |
| `MOVIEMIND_CORPUS_SHA256` | recommended | Refuse to build if the artifact does not match |

Nothing else is required. `Settings` already has working defaults for the
corpus path, `k`, `limit`, query length and CORS origins, and no corpus-path
variable is set for the deployment.

**Not required, and deliberately absent: `TMDB_API_READ_ACCESS_TOKEN`.** The API
never talks to TMDb. The token is read by exactly one program,
`scripts/fetch_tmdb_snapshot.py`, which runs on a laptop, is excluded from the
function bundle, and must never be given to the deployment platform. If you find
yourself wanting to add it, the deployment is trying to fetch data it should
have been handed.

**`.env` is excluded twice, and the second gate is the one that matters.**
`.vercelignore` keeps `.env` out of the source *upload*, which is what a cloud
build receives. It is **not** re-applied to a local `vercel build` — that runs
against the working tree — so on a laptop the file was landing in the function
bundle until `.env` and `.env.*` were added to
`functions["moviemind/api/app.py"].excludeFiles`. Verified against the built
`filePathMap`: `.env` and `.env.local` are absent, `data/processed/movies.jsonl`
is present. Treat `excludeFiles` as the security boundary and `.vercelignore` as
size hygiene.

`MOVIEMIND_CORS_ORIGINS` does not need setting either: the frontend is
same-origin, so no cross-origin request is made. The default allowlist is four
localhost origins and never a wildcard.

---

## 5. Deploying

`vercel build` has been run locally and passes (§8). `vercel deploy` has **not**
been run and the repository has not been pushed. The project *is* linked, so
steps 1–3 below are already done and are kept for reproducing them from scratch.

```bash
# 0. Preconditions: everything green, and the corpus validated locally
python -m pytest -q
python -m ruff check moviemind tests scripts deploy
python -m mypy moviemind deploy
(cd frontend && npm test && npm run typecheck && npm run lint)
python deploy/fetch_corpus.py
python deploy/stage_frontend.py
python deploy/prune_venv.py
python deploy/audit_build.py

# 1. Publish the corpus to a non-public location, then record its hash
python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('data/processed/movies.jsonl').read_bytes()).hexdigest())"

# 2. Create the project and link it to the repository
npm i -g vercel
vercel link                       # or: import https://github.com/lapsecoder/MovieMind at vercel.com/new
vercel pull --yes --environment=production

# 3. Add the build-time variables (values come from your shell, so the token is
#    never typed into a dashboard paste buffer or committed)
vercel env add MOVIEMIND_CORPUS_URL      production
vercel env add MOVIEMIND_CORPUS_TOKEN    production
vercel env add MOVIEMIND_CORPUS_SHA256   production
# VERCEL_SUPPORT_LARGE_FUNCTIONS is NOT set. deploy/prune_venv.py brings the
# bundle under the 225 MB ceiling, so the beta escape hatch is not needed.

# 4. Build once without publishing, and read the log
vercel build

# 5. First deployment -- inspect it in a private browser window before sharing
vercel deploy
vercel deploy --prod
```

If a future dependency pushes the bundle back over 225 MB, `VERCEL_SUPPORT_LARGE_FUNCTIONS=1`
remains available as a Vercel-wide escape hatch (`vercel env add VERCEL_SUPPORT_LARGE_FUNCTIONS production`).
It is a beta, so treat it as a fallback rather than the design.

To update the corpus later, without touching code: replace the artifact, update
`MOVIEMIND_CORPUS_SHA256`, redeploy. The API ToU cap on cached TMDb content is
**six months**; treat that as the refresh deadline, and re-read the terms before
it (`docs/phase-02-dataset-audit.md` §15).

---

## 6. Configuration files

| File | What it does |
| --- | --- |
| `vercel.json` | Framework preset, build command, function duration/memory/bundle trim, security headers |
| `.vercelignore` | Keeps `.env`, `data/`, `tests/`, `docs/` and caches out of the source upload. Vercel's built-in defaults cover `.env.local` but **not** a plain `.env`, so it is listed explicitly |
| `.python-version` | Pins 3.12, a version Vercel's Python runtime supports. `requires-python = ">=3.11"` alone is not a pin |
| `pyproject.toml` | `[tool.vercel] entrypoint` only. No `[build-system]`, no `requirements.txt` |
| `deploy/fetch_corpus.py` | Build-time corpus input. Standard library only |
| `deploy/stage_frontend.py` | Copies `frontend/dist` to `public/`. Standard library only |
| `deploy/prune_venv.py` | Deletes vendored `tests/` fixtures and `pip` from the resolved venv before it is packed. Standard library only, refuses to prune a protected distribution, and fails if it frees less than 8 MB |
| `deploy/audit_build.py` | Pre-deploy audit: no credential in anything that ships, no corpus in the served static output, no filesystem route |

`frontend/` and `moviemind/` were not modified. `vercel.json` sets no
`rewrites`, because the frontend has one page and no client-side router: static
paths hit the CDN, `/api/v1/*` reaches the function, and an unknown path gets
the API's own 404 envelope rather than a page.

### Why `public/` and not `outputDirectory`

`vercel.json` originally set `"outputDirectory": "frontend/dist"`. **The build
succeeded and produced no frontend at all.** Two independent observations
established that it was being ignored:

* `.vercel/output/builds.json` listed the static input as `src=public/**/*`.
* `.vercel/output/static` did not exist, and the generated `config.json` rewrote
  every non-API path to `/404.html` and everything else to the function.

Vercel's FastAPI documentation is explicit that a backend preset serves static
assets from a `public/` directory at the project root, and that `public` is not a
valid `vercel.json` key. `deploy/stage_frontend.py` therefore republishes the Vite
output into `public/` as the last build-command step. It fails closed if
`frontend/dist/index.html` is missing, so a build can never silently ship a site
that 404s on `/`.

After that change `.vercel/output/static` contains `index.html` plus the two
hashed assets, and `public/**` is also in `excludeFiles` so the bundle does not
carry a second copy of the frontend.

---

## 7. Licensing assumptions and limitations

Re-read on 2026-09-30. **This is not legal advice, and it does not claim more
permission than the sources support.**

* **Non-commercial.** The deployment has no ads, no accounts, no payments, no
  analytics and no tracking. TMDb's API ToU §1.A and main ToU §3.A permit
  personal, non-commercial use; anything monetised would need a separate written
  agreement, and the licence could be terminated at TMDb's sole discretion.
* **Attribution is displayed, not merely present.** The verbatim notice
  ("This product uses TMDB and the TMDB APIs but is not endorsed, certified, or
  otherwise approved by TMDB.") is served by `/api/v1/meta`, rendered by the
  frontend, and present in the built JS bundle. TMDb's ToU §3 also requires the
  TMDb logo. The application currently renders the text notice; **the logo
  requirement should be confirmed with TMDb before the deployment is announced.**
  That is a real open item, not a formality.
* **Retrieval, not training.** No model is trained. The TF-IDF representation is
  a deterministic function of the corpus, computed at startup. The staff
  guidance recorded in `docs/phase-02-dataset-audit.md` §4 — informal, in a
  public forum, and not a contract amendment — treats this as retrieval. It
  removes the apparent prohibition; it does not guarantee anything.
* **No redistribution.** Serving recommendations from a server-side index is
  not redistribution. Publishing the corpus, or mounting it as a static file,
  would be. Both are excluded here, and `deploy/audit_build.py` fails the build
  if a `.jsonl` file ever appears in the static output.
* **Six-month refresh.** The ToU caps cached content at six months. The corpus
  is dated 2026-09-26, so the refresh deadline is **2027-03-26**.
* **Vercel's own terms.** The Hobby plan is free and is intended for personal,
  non-commercial use, which matches. Vercel — not the author — determines
  whether a given project is commercial.
* **Re-read before changing anything.** Monetisation, a public corpus, a
  different distribution model, or moving the token onto the platform: all of
  these require re-reading the terms first.

---

## 8. Local verification

Run on 2026-09-30 against the real 5,000-film corpus, with the backend started
exactly as it would be in the deployment
(`uvicorn moviemind.api.app:app` from the repository root, no configuration).

| Check | Result |
| --- | --- |
| Frontend production build | `tsc -b && vite build` clean. `frontend/dist` = 953 B html + 250,248 B js + 18,710 B css = **270 kB** |
| Backend starts | Clean; no warnings |
| `/api/v1/health` | `200 {"status":"ok","service":"moviemind-api","api_version":"v1"}` |
| `/api/v1/ready` | `200 ready`, 5,000 films, 23,404 dimensions, 226,352 nonzeros, density 0.19343%, **0.41 s** load |
| `/api/v1/meta` | fingerprint `af683a71bb38bf690…` (unchanged); TMDb notice verbatim; no credential in the body |
| Search | `?q=matrix&limit=3` → 3 matches, deterministic, titles only |
| Details | `?id=603` → The Matrix (1999), 15 documented fields, no `tokens` or `document` |
| Recommendations, k=5 | 5 results, `min_shared_terms=3`, `exhausted=false`: The Matrix Resurrections 0.4823, Cloud Atlas 0.3374, The Animatrix 0.2081, Ghost in the Shell 0.1144, John Wick: Chapter 3 0.1084 |
| k=5 is a prefix of k=10 | Holds — changing k is a new server-side request, never a client re-slice |
| k ceiling | `k=50` accepted, `k=51` → `422 invalid_k` with `min_k`/`max_k`/`default_k` |
| Corpus not downloadable | 7 probes (`/data/processed/movies.jsonl`, `/movies.jsonl`, `/data/raw/tmdb_movies.jsonl`, `/api/v1/movies.jsonl`, `/../data/...`, `/%2e%2e/data/...`, `/.vercel/output/functions/.../movies.jsonl`) all returned the standard 404 envelope |
| No token in the build | Built bundle contains no `TMDB_API`, no `themoviedb.org`, no token value. Only off-origin strings are `http://placeholder` (the client module's internal URL base, unused because the configured base is empty), the `http://www.w3.org` XML namespace and `https://react.dev` in a React error message |
| Same-origin API path | `VITE_API_BASE_URL` is not set and not inlined; the bundle uses relative `/api/v1/...` |
| Memory | 80 MB resident after loading all 5,000 films, against 1024 MB provisioned |
| `deploy/fetch_corpus.py` | 4 paths exercised against a local authenticated server: token + checksum success; wrong token → `403` and nothing written; checksum mismatch → refused; 404 → refused. No `.partial` file left behind in any failure case |
| `deploy/audit_build.py` | Positive: clean. Negative: fails and names the file when a fake token is planted in `public/`, and when a `movies.jsonl` is planted in `public/` |

### `vercel build`

Run locally on 2026-10-01 against the real corpus. `vercel deploy` has **not**
been run.

| Check | Result |
| --- | --- |
| Build | `Build Completed in .vercel\output`. Python 3.12 from `.python-version`, `uv.lock` honoured, `npm ci` + `tsc -b` + `vite build` clean |
| Static output | `.vercel/output/static` = `index.html` + `assets/index-*.js` + `assets/index-*.css` |
| Function bundle | `.vc-config.json` `filePathMap`, 4,719 entries |
| Corpus in bundle | `data/processed/movies.jsonl` **present** |
| App code in bundle | `moviemind/api/app.py`, `moviemind/recommend.py`, `pyproject.toml` **present** |
| `.env` in bundle | `.env`, `.env.local`, `.env.example` **absent** |
| Trimmed from bundle | `data/raw`, `data/audit`, `tests/`, `docs/`, `frontend/`, `public/` **absent** |
| `excludeFiles` length | 215 characters, against a 256 limit. All alternatives must sit in **one** brace group; Vercel uses `micromatch`, and a comma-separated list outside the braces silently matches nothing |
| `excludeFiles` reach | Applied as `glob("**", { cwd: workPath })` in the builder, so it only ever matches **project** paths. It cannot trim vendored site-packages, which is why `deploy/prune_venv.py` exists |
| Size ceiling | **Passes at 183.8 MB against 225 MB**, with no large-functions flag and no `Bundle size ... exceeds the standard size` warning. Was 254.48 MB (`Total bundle size (254.48 MB) exceeds the maximum function size`) before the three fixes below |

### Where the bundle size actually goes

Measured by aggregating the `filePathMap` against the resolved virtualenv. It
started at 241.5 MB over 4,719 entries and now stands at **183.8 MB over 2,547**:

| Source | MB | Disposition |
| --- | --- | --- |
| `scipy` + `scipy.libs` | 90.2 | **required** — `recommend.py` uses `scipy.sparse` |
| `numpy` + `numpy.libs` | 45.7 | **required** |
| `sklearn` + `scikit_learn.libs` | 23.9 | **required** — the index is built at startup |
| `_uv/uv` | 56.3 | Vercel's own build-time `uv` binary. Not imported at request time; excluded |
| vendored `tests/` fixtures | 28.4 | unreachable from any request; pruned |
| `pip` | 5.3 | never imported at runtime; pruned |
| `uvicorn[standard]` extras | 22.9 | unused — the runtime vendors its own uvicorn |
| `data/processed` reports | 1.8 | `evaluation_labels.json`, `experiments.json`, `experiment_manifest.json` are Phase 2/4 analysis output; excluded |
| `data/processed/movies.jsonl` | 8.6 | **required** — the corpus |

Three things worth recording, because all three were wrong before this was
measured. **pandas is not in the bundle** at all — it is not a dependency, and
the estimate that put it near 60 MB was wrong. **scikit-learn cannot be
dropped**: `service.py` → `Recommender` → `RecommenderIndex.build` →
`build_representation` → `TfidfVectorizer`, so all three scientific packages are
on the serving path, and removing them would mean reimplementing TF-IDF.
And **`excludeFiles` cannot trim vendored packages**, so the 28 MB of test
fixtures could only be removed by pruning the venv before it is packed.

One residual risk, stated rather than hidden: excluding `_uv/uv` was verified by
a successful local build, but the resulting Linux bundle cannot be executed on
this Windows machine. The first deployment should be smoke-tested before it is
shared.

Local-only notes: Vercel CLI 62.1.0 requires `uv` on `PATH`, and on this Windows
machine it also needs a space-free path to work around a quoting bug in
`uv python list`. Both are environment issues, not repository ones.

### Test results after the change

| Suite | Command | Result |
| --- | --- | --- |
| Python | `pytest -q` | **331 passed** |
| Python | `ruff check moviemind tests scripts deploy` | All checks passed |
| Python | `mypy moviemind deploy` | No issues, 17 files |
| Frontend | `npm test` | **77 passed** |
| Frontend | `npm run typecheck` | clean |
| Frontend | `npm run lint` | clean |

Unchanged from the pre-deployment baseline. No test was added, removed, edited
or skipped; the deployment added configuration, and `tests/` is excluded from
the deployment bundle so it cannot drift from what CI runs.
