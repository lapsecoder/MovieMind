# Phase 8 - Final QA and Portfolio Readiness

**Status: PASS.** Repository initialised and audited, security re-verified, all
quality gates green, the real application driven in a real browser, and two
small genuine accessibility defects found and fixed. The Phase 4 methodology,
the locked configuration, and the 5,000-film corpus are unchanged.

**No commit was made.** The index was staged and audited; the working tree is
left for review.

---

## 1. Scope

Phase 8 added no feature and changed no recommendation behaviour. It did the
work needed to hand the project to someone else:

1. initialise version control and make the ignore rules real;
2. audit for secrets and confirm the TMDb credential is contained;
3. re-run every quality gate;
4. drive the real application in a real browser, including accessibility checks
   that jsdom cannot perform;
5. fix anything genuinely broken, with regression tests;
6. write the documentation a portfolio reviewer needs;
7. stage the result and verify what is actually going in.

---

## 2. Repository initialisation

| Item | Before | After |
| --- | --- | --- |
| `.git` directory | absent | present at `D:\MovieMind`, single repository |
| Nested repositories | none | none; `frontend/` is not a separate repo |
| Branch | n/a | `master` |
| Commits | n/a | **0 — nothing committed** |

A SHA-256 baseline of all 101 project files (excluding `.git`, `node_modules`,
`dist`, and tool caches) was captured before any change, giving byte-level change
detection for the question that actually matters: *did any backend, data, or
artifact file change?*

### 2.1 Ignore rules made real

Before initialisation the root `.gitignore` documented intent and enforced
nothing. Phase 7 could not verify "no secrets staged" or "raw data untracked"
because nothing was tracked. That limitation is now closed.

`.gitignore` was extended to cover the Node and frontend build surface that did
not exist when it was first written:

* `frontend/node_modules/`, `frontend/dist/`, `frontend/coverage/`
* `*.tsbuildinfo`, Vite's dependency cache
* editor, OS, and temporary-file patterns
* a stale `Thumbs.db:encryptable` typo, corrected to `Thumbs.db`

Verified behaviour against the real index:

| Path | Expected | Result |
| --- | --- | --- |
| `.env` | ignored | ignored — holds the only real token |
| `frontend/.env` | ignored | ignored |
| `.env.example` | **tracked** | trackable — empty value, safe |
| `data/raw/` | ignored | ignored — snapshot not redistributable |
| `data/processed/` | ignored | ignored — 5,000-line corpus not committed |
| `data/audit/` | ignored | ignored |
| `data/.gitkeep` | **tracked** | trackable |
| `frontend/node_modules/`, `dist/` | ignored | ignored |
| caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`) | ignored | ignored |
| `frontend/coverage/`, `*.tsbuildinfo` | ignored | ignored |

No data file, artefact, credential, build output, or dependency is eligible for
staging.

---

## 3. Security audit

### 3.1 Credentials

| Check | Result |
| --- | --- |
| Real TMDb token present | yes, in local `.env` only — gitignored, never committed, never printed in any report |
| `TMDB_API_KEY` set anywhere | no; `.env.example` documents it commented out |
| Token in any tracked file | none |
| Token in `frontend/dist/` | none — 3 files scanned, 0 hits |
| Private keys, AWS keys, GitHub/Slack tokens, basic-auth URLs, `sk-` keys | none anywhere in the project |

A broad scan for the JWT prefix `eyJ` matched one location:
`frontend/package-lock.json:4015`. That is an npm `integrity` SHA-512 value, not
a credential. The lock file was **not** modified — "fixing" it would break
reproducibility.

TMDb credential handling is confined to `scripts/fetch_tmdb_snapshot.py`, the one
script that fetches a snapshot. The API reads no TMDb credential and makes no
outbound network call.

### 3.2 Build output

`frontend/dist/` was regenerated in this phase and scanned: 3 files, 0 secret
matches. It contains no absolute backend URL — the client emits same-origin
`/api/v1/...` paths only.

### 3.3 Code hygiene

No `TODO`, `FIXME`, or `HACK` markers. No `console.log` left in production code.
No empty files, no editor backups, no stray binaries. One leftover probe file
(`moviemind/zz_probe/x.pyc`, created by an earlier verification script) and one
accidental `__pycache__` directory were removed.

---

## 4. Quality gates

All green, run in this environment against the final state.

### 4.1 Backend

| Gate | Command | Result |
| --- | --- | --- |
| Tests | `pytest -q` | **PASS** — 331 passed in 9.22 s |
| Lint | `ruff check moviemind tests scripts` | **PASS** — all checks passed |
| Types | `mypy moviemind` | **PASS** — no issues in 15 source files |

### 4.2 Frontend

| Gate | Command | Result |
| --- | --- | --- |
| Types | `npm run typecheck` | **PASS** — exit 0 |
| Lint | `npm run lint` | **PASS** — exit 0 |
| Tests | `npm test` | **PASS** — 2 files, **77 passed** |
| Build | `npm run build` | **PASS** — 43 modules, 914 ms |

Test count moved from 73 to 77: four regression tests added in this phase for the
defects in section 6. Per file, 45 in `app.test.tsx` and 32 in `client.test.ts`.

The React `act(...)` warnings that appeared in the first run of the new tests
were resolved rather than suppressed. Two pre-existing tests asserted
synchronously after `render(<App />)` while a `/meta` fetch was still settling;
they now await the first async-dependent element. Warning count: 6 → **0**.

Build output: `index.html` 0.95 kB, CSS 18.71 kB (4.61 kB gzipped), JS 250.25 kB
(77.67 kB gzipped). No warning emitted by Vite.

### 4.3 Invariants preserved

| Invariant | Verification | Result |
| --- | --- | --- |
| Configuration fingerprint | `/api/v1/meta` | `af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7` — unchanged |
| Corpus size | line count of `data/processed/movies.jsonl` | 5,000 lines |
| Corpus SHA-256 | recomputed | `53b0b8c675651ea438230fe75bba3660f52213428876f9cb99e565825fcdece0` — matches Phase 4 |
| Backend/data files modified | SHA-256 baseline | none |
| Selected constants | `moviemind/experiments.py` | `SELECTED_GENRE_WEIGHT = 0.44`, `SELECTED_MIN_SHARED_TERMS = 3` — unchanged |

`docs/` files are valid UTF-8 with no replacement characters; the phase reports
are unmodified apart from the historical notes in section 9.

---

## 5. Real-browser verification

Microsoft Edge (`msedge.exe`, headless) was driven over the DevTools Protocol
using Node 24's built-in `WebSocket`. **No npm dependency was added** — Phase 8
does not change the dependency set. The real FastAPI backend ran on `:8000` and
the real Vite dev server on `:5173`, serving the real 5,000-film corpus.

### 5.1 Functional flow — 36/37

| Check | Result |
| --- | --- |
| Page loads and reaches the running app | PASS |
| App name rendered | PASS |
| TMDb attribution visible on load | PASS |
| Search returns results via real typing | PASS |
| Result options rendered | PASS |
| Polite live region announces the result count | PASS |
| ArrowDown moves focus into the result list | PASS |
| Details panel renders after selection | PASS |
| Runtime formatted, release year shown | PASS |
| `feature_counts` rendered | PASS |
| Missing overview communicated in the UI | PASS |
| Recommendations render automatically on selection | PASS |
| Recommendation rows rendered, ranks in order | PASS |
| Similarity shown as a raw score, not a percentage | PASS |
| Shared-term evidence on every row | PASS |
| Examined-versus-returned denominator shown | PASS |
| Skipped candidates as a tally, not a list | PASS |
| **DOM order matches backend order exactly** | PASS |
| k control offers supported options; k=5 narrows | PASS |
| Active k exposed via `aria-pressed` | PASS |
| Escape clears query and selection | PASS |
| New search works after a previous result | PASS |
| No-results state explained, not blank | PASS |
| No horizontal overflow at 1440 / 768 / 390 px | PASS |
| Single-column layout on mobile | PASS |
| No console errors or uncaught exceptions | PASS |
| Browser talks only to its own origin | PASS |
| No TMDb request issued by the browser | PASS |
| No direct browser call to the backend port | PASS |
| All API calls on the same-origin `/api/v1` path | PASS |
| No failed network requests | see below |

### 5.2 The one reported failure is not a defect

The final check reported two `net::ERR_ABORTED` events and is recorded here as a
**false positive**, with the evidence.

Both aborted requests were `/api/v1/meta` and `/api/v1/ready`, fired during
initial load. A dedicated second script captured the outcome of each:

```
/meta  : 2 outcome(s) -> FAILED net::ERR_ABORTED (canceled=true), HTTP 200
/ready : 2 outcome(s) -> FAILED net::ERR_ABORTED (canceled=true), HTTP 200
```

Each request is issued **twice**: React 19's development `StrictMode` mounts
effects twice, and the effect cleanup aborts the first in-flight request. The
remount issues it again and receives HTTP 200. The first request was correctly
cancelled by its own cleanup — no leaked work, no duplicate data, nothing
user-visible. `StrictMode` does not double-invoke in production, so this cannot
occur in a deployed build.

This is expected, correct behaviour, not a network failure. Counting a
developer-only cancelled request as a defect would be a false alarm, so the
finding is documented rather than "fixed".

### 5.3 Layout, measured rather than eyeballed

Screenshots were captured at three widths into
`%TEMP%\opencode\p8-shots\` (`desktop.png`, `desktop-bottom.png`, `mobile.png`,
`tablet.png`, 36.9–95.0 kB, proving real rendering). Because this environment
cannot view images, layout correctness was established **numerically** instead —
more precise than inspection:

| Measurement | 1440 px | 768 px | 390 px |
| --- | --- | --- | --- |
| Grid columns | 2 (`453px 635px`) | 1 (`720px`) | 1 (`358px`) |
| Horizontal overflow | none | none | none |
| Details panel width | 419 px | 686 px | 324 px |
| Recommendations list height | 1094 px | — | — |
| Recommendation row height | 104 px | — | — |

Additional confirmations: `h1` renders at 18 px / weight 600 (styled, not the
browser default 2em/400); the font stack is a system stack with no webfont
download; 16 focusable controls, none removed from the tab order; no element
traps focus.

### 5.4 Accessibility sanity check — 11/11

| Check | Result |
| --- | --- |
| Focus ring on every keyboard stop | PASS — 17/17 tab stops, 2 px solid accent ring, `:focus-visible` matching |
| No visible control below 24×24 (WCAG 2.2 AA, 2.5.8) | PASS — 0 of 15 |
| Exactly one `h1` | PASS |
| Every interactive control has an accessible name | PASS |
| `main` landmark present exactly once | PASS |
| `html` has a `lang` attribute; non-empty document title | PASS |
| `combobox` / `listbox` roles present | PASS |
| Polite live region present | PASS |
| Heading levels never skip | PASS — `h1 → h2 → h3 → h2 → h2` |
| Image alt text | N/A — the app renders no images |
| No keyboard trap | PASS — all visible controls tabbable |

An earlier run of the focus check reported that the search input had **no**
focus indicator. That was a flaw in the check, not the app: it focused the
element programmatically, and `:focus-visible` deliberately does not match
programmatic focus. Re-tested with real `Tab` keypresses, all 17 stops show the
ring.

---

## 6. Defects found and fixed

Two genuine defects were found by the browser audit. Both are small, both are
accessibility-related, and both are fixed with a regression test.

### 6.1 Skip link collapsed to line-height when focused

`frontend/src/index.css` — `.sr-only` sets `padding: 0`, and the
`.sr-only-focusable:focus` reset restores position, size, and clip but **not
padding**. The `px-3 py-2` on the element were overridden, so the revealed skip
link measured 132×19 px — below the 24 px minimum.

Measured in the browser before the fix:

```
unfocused : {"pad":"0px","w":1,"h":1}
FOCUSED   : {"pad":"0px","w":1,"h":1}   ← padding never restored
```

Fixed by restoring padding in the reveal utility. This matters beyond the size
number: the skip link is the first focusable element, so its focusability and
visibility are the entry point for keyboard navigation.

### 6.2 The k=5 button was 23×24 px

`frontend/src/components/Recommendations.tsx` — the option buttons sized
themselves from `px-2 py-1` plus their label, so the single-glyph "5" measured
23 px wide, one pixel under the minimum. Added `min-h-6 min-w-6` so every option
has a 24 px floor.

After both fixes, the browser reports 0 of 15 visible controls below 24×24.

### 6.3 Display bug in the About panel

`frontend/src/components/AboutPanel.tsx` — the template read
`API v{meta.api_version}` while `/meta` already returns `"v1"`, so the panel
rendered **`API vv1`**. Confirmed against the live endpoint before changing
anything:

```
backend /meta api_version : "v1"
rendered in About panel   : "API vv1 · config af683a71bb38"
```

Now renders `API v1 · config af683a71bb38`. A test asserts the version is
printed exactly as sent and that no `vv` prefix appears.

### 6.4 Regression tests added

| Test | Guards |
| --- | --- |
| `gives every k option a 24px pointer target floor` | the `min-w-6 min-h-6` fix |
| `keeps the skip link on a padded reveal utility` | the skip-link classes |
| `declares a focus ring at the base layer so no control can miss it` | the global `:focus-visible` rule and the padding restore in `index.css` |
| `prints the API version exactly as the backend sends it` | the `vv1` fix |

Frontend tests: 73 → **77**.

---

## 7. Documentation

| File | Status |
| --- | --- |
| `README.md` | **new** — what it is, how retrieval works, project layout, setup, tests, all nine known limitations, data/licensing, security notes |
| `docs/architecture.md` | **new** — layers, the three structural separations, request lifecycle, one-origin design, state and its absence, error handling, build-time data flow, testing strategy, deliberate absences |
| `docs/phase-08-final-qa.md` | **new** — this document |
| `docs/phase-01..07` | unchanged except the historical notes below |

`pyproject.toml` intentionally declares **no build backend** — MovieMind runs from
the repository root. `pip install -e .` therefore fails by design, so the README
documents the direct dependency install instead. Verified: `pip install -e .`
fails with a setuptools package-discovery error, and all declared dependencies
import cleanly in the environment that ran the gates.

Every claim in the README was checked against the running system or the source:
the fingerprint, corpus size, and corpus SHA-256 from the live API and a
recomputation; the selected constants from `experiments.py`; the six endpoints
from `app.py`; the per-file test counts from a verbose Vitest run.

---

## 8. Staged result

`git add -A` was run and the index audited. Nothing was committed.

**92 files, 25,163 lines** staged — the entire project, since this is the first
commit that will ever exist. Staging a path audit was run over the result:

| Audit | Result |
| --- | --- |
| Secrets in staged content | **0 real** (see below) |
| `data/raw/`, `data/processed/`, `data/audit/` staged | **0** — only `data/.gitkeep` |
| `node_modules/`, `dist/`, `coverage/` staged | **0** |
| Caches (`__pycache__`, `.pyc`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`) staged | **0** |
| `.env` staged | **no** |
| `.env.example` staged | yes — root and `frontend/`, both fully commented or empty |
| Nested `.git` directories | none |

The secret scan over all 92 staged files reported seven raw pattern matches. Each
was inspected individually and all seven are benign:

| Location | Match | Verdict |
| --- | --- | --- |
| `.env.example` | `TMDB_API_READ_ACCESS_TOKEN=` | template, **empty value** |
| `docs/phase-02-dataset-audit.md` (×2) | `<your v4 read access token>`, `<your v3 api key>` | documentation placeholders |
| `scripts/fetch_tmdb_snapshot.py` | `<your v4 read access token>` | placeholder inside an error message |
| `tests/test_api.py` | `TMDB_API_READ_ACCESS_TOKEN=leak` | a **negative test** asserting a token is *not* serialised into `Settings` output; the literal `leak` is the assertion subject |
| `docs/phase-08-final-qa.md` (×2) | quoted placeholders | this table, quoting the placeholders it documents — how they were identified |
| `frontend/package-lock.json` | `eyJ…` | npm `integrity` SHA-512 hash; **not modified** |

The live token exists only in the gitignored `.env`. No private key, cloud
credential, or provider token is staged.

Line-ending note: Git reports `LF will be replaced by CRLF` for every text file,
which is Windows `core.autocrlf` behaviour, not a staged-content change. The blob
in the index is LF; only the working copy is affected on checkout.

Source changes made in this phase, all now staged: `.gitignore`,
`frontend/src/index.css`, `frontend/src/components/Recommendations.tsx`,
`frontend/src/components/AboutPanel.tsx`, `frontend/src/test/app.test.tsx`,
plus the three new documents.

---

## 9. Documentation consistency

Two statements in earlier reports became false once Phase 8 initialised Git and
found a browser. Rather than rewrite history, both are annotated:

* `docs/phase-07-integration-qa.md` opened with "Phase 8 has NOT started" and
  listed "no version control" and "no real-browser verification" as known
  limitations. Each now carries a note stating it records the state *during
  Phase 7*, with the resolution. The measured Phase 7 results are untouched.
* `docs/phase-01-dataset-strategy.md:373` states the repository is not under
  version control. That was accurate when written and is left alone as a
  historical record.

`docs/phase-01..07` all remain valid UTF-8 with no replacement characters.

---

## 10. Known limitations

Carried forward from earlier phases, restated in `README.md`:

1. **The catalogue is 5,000 films, not all of TMDb.** Many famous films return
   nothing. Most likely source of "why does it have nothing for X?".
2. **No relevance ground truth.** Every Phase 4 score is agreement with a
   structural proxy, not human judgement.
3. **49.3% of top-10 results have thin evidence.** A deliberate trade for
   `min_shared_terms = 3`.
4. **Cast-based search is poorly served** (recall@10 of 0.157 inside
   representation D). Search covers titles only, by design.
5. **No user-facing filtering or sorting.** Engine order is presented as-is.
6. **Not real-time.** Static snapshot; rebuilding features is a batch operation.
7. **No authentication, no database, no persistence.** All requests stateless.
8. **Untested on physical assistive hardware.** Verified programmatically in a
   real browser, not with a screen reader or switch device.
9. **The snapshot is not redistributable.** TMDb data is free for non-commercial
   use with mandatory attribution; commercial use needs a written agreement.

New to this phase:

10. **The processed corpus is not committed.** A fresh clone must fetch or supply
    `data/raw/` and run `scripts/build_features.py` before the API can start.
    This is deliberate — the artefacts are large and derived, and the raw
    snapshot is not redistributable — but it does make the setup longer than a
    one-command clone.
11. **No commit was made.** The index is staged and audited but not committed, so
    there is no history to inspect yet.
12. **Two known dependency advisories remain.** `npm audit` reports two moderate
    findings in the frontend tree, and `eslint@9.39.5` is deprecated upstream.
    Neither is a defect in this project's code, and neither was touched: fixing
    them would change the dependency set in a phase whose scope is QA.
13. **Screenshots were not visually inspected.** This environment cannot view
    images. Rendering is proven by the captured PNGs and verified numerically by
    the geometry measurements in section 5.3, which is stronger for the specific
    properties checked but is not a substitute for a human eye for typography
    and colour.

---

## 11. Verdict

| Area | Result |
| --- | --- |
| Repository initialised, ignore rules verified | **PASS** |
| No secrets staged or committed | **PASS** |
| Backend tests / lint / types | **PASS** — 331 / clean / clean |
| Frontend types / lint / tests / build | **PASS** — 0 / 0 / 77 / 43 modules |
| Configuration fingerprint unchanged | **PASS** |
| Corpus size and SHA-256 unchanged | **PASS** — 5,000 lines, hash matches |
| Methodology untouched | **PASS** — no file under `moviemind/`, `data/`, `tests/`, `scripts/` modified |
| Real-browser functional flow | **PASS** — 36/37, the one flag a documented dev-mode false positive |
| Layout at three viewports | **PASS** — no overflow, correct breakpoints |
| Accessibility sanity | **PASS** — 11/11 after two fixes |
| Defects found and fixed with tests | **2 accessibility + 1 display, 4 regression tests** |
| Documentation written | **PASS** — README, architecture, this report |
| Staged result audited | **PASS** — 0 secrets, 0 data, 0 build output |
| Committed | **No** — staged for review, as instructed |

**Overall: PASS.** The system is consistent, measurable, reproducible from its
snapshot, and honest about what it does not do. Phase 8 introduced no feature and
changed no recommendation behaviour.
