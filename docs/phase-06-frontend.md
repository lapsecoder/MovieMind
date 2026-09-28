# Phase 6: Frontend

A single-page client for the MovieMind API built in Phase 5. It lets a user find a
film by title, read its metadata, and see other films the engine considers
similar — with the evidence behind every claim on screen.

This phase adds no backend behaviour. `moviemind/` is byte-identical to its
Phase 5 state, and the Phase 4 artefacts under `data/processed/` are untouched.

---

## 1. Scope and stack

| Choice | Value | Why |
| --- | --- | --- |
| Framework | Vite 7 + React 19 | Selected over Next.js. There is no SSR need: every byte of the page depends on a runtime API call, and the catalogue is private to a local backend. A static SPA is the smallest thing that does the job. |
| Language | TypeScript 5.9, `strict` | The API contract is the thing most likely to drift. `strict` plus hand-written types mirroring `schemas.py` makes a contract change a compile error. |
| Styling | Tailwind CSS v4 via `@tailwindcss/vite` | No runtime CSS-in-JS, no preprocessor step, no config file to keep in sync. |
| Tests | Vitest + Testing Library | Shares Vite's transform pipeline, so tests and the app compile identically. |
| State | React `useState`/`useEffect`, three hooks | No state library. The app has one selection id and three async reads; a store would be a second source of truth for no benefit. |
| Data layer | Hand-written client over `fetch` | No React Query, no MSW. Four endpoints and three calls; the caching a library would add is not the hard part. The hard part is cancellation, and that is a dozen lines (sec 2). |

Runtime dependencies: `react`, `react-dom`. Everything else is dev-only.
Production bundle: **250 kB / 78 kB gzipped** (React + app, one chunk).

---

## 2. Architecture

```
src/
  config.ts            the only place a URL or a tunable is written down
  api/
    types.ts           wire types, mirroring moviemind/api/schemas.py field for field
    errors.ts          the error envelope, and three distinct failure classes
    client.ts          the only fetch() in the application
  hooks/
    useMovieSearch.ts  debounced, cancellable search
    useMovieDetails.ts metadata for the selected film
    useRecommendations.ts  ranked results for the selected film
    useAbout.ts        /meta and /ready, for attribution and the status pill
  components/
    SearchPanel.tsx    input, status line, results
    MovieDetails.tsx   selected film
    Recommendations.tsx ranked results and the evidence summary
    AboutPanel.tsx     attribution, limits, and the engine's own caveats
    primitives.tsx     skeletons, empty/error states, chips, score readout
  App.tsx              one selection id, wired to the three hooks
```

### 2.1 One client, one base URL

Every request goes through `api/client.ts`. No component imports `fetch`, and no
component contains a URL. That makes four requirements enforceable in one place
instead of by convention: base URL, error mapping, timeouts, and cancellation.

### 2.2 Cancellation and staleness

The single hardest problem in this app is a search box. Three mechanisms, all
tested:

1. **Debounce (250 ms).** Not to protect the server — a title search measures
   0.55 ms — but to avoid a request per keystroke, which flickers the loading
   state and reorders results mid-type.
2. **Abort.** Each request gets an `AbortController`; a new query aborts the
   previous one. A superseded request resolves to silence, never to an error
   state, because an error panel flashed on every keystroke would be unusable.
3. **Sequence guarding.** A response already on the wire cannot be cancelled, so
   each request carries a monotonic token and any late arrival is discarded.
   `app.test.tsx` reproduces this deliberately: the first query is answered late
   with an empty result set while the second is answered quickly with results,
   and the assertion is that the *newer* results survive.

### 2.3 Derived state, not assigned state

The three data hooks do not call `setState` synchronously inside an effect. Each
stores one result tagged with the key it belongs to, and derives the rendered
state during render:

```
key is null                     -> idle
stored result is for another key -> loading
otherwise                       -> the stored state
```

Three things follow. `loading` appears on the first render of a new query rather
than one render later. Clearing the query needs no `setState` at all, so there is
no cascade of re-renders. And a stale response is harmless, because it is tagged
with a key that no longer matches. This structure is also what the
`react-hooks/set-state-in-effect` rule is pushing towards, so the lint gate
enforces it rather than leaving it to review.

---

## 3. No duplicated recommendation logic

**The client does not rank, score, filter, or re-order anything.** The engine's
ordering is reproduced verbatim: the results array is rendered in array order
using the `rank` the API supplied.

This is a decision, not an omission. A second ordering rule in TypeScript is a
second set of answers to the same question, and it would drift the moment the
engine's tie-breaking changed — silently, with no failing test. Three specific
rejections:

- **Re-sorting by `score`.** Rejected. The engine's order already encodes the
  `min_shared_terms` evidence threshold, and sorting by raw cosine would
  interleave thin and thick evidence matches.
- **Re-slicing results when `k` changes.** Rejected. The engine re-ranks over a
  wider candidate set, so the `k=5` response is not in general a prefix of the
  `k=10` response. Slicing would show a different answer under the same number.
- **Recomputing the score to display it.** Rejected. The value is displayed
  exactly as sent.

`app.test.tsx` pins this by comparing rendered titles against the fixture's
`recommendations` order and `rank` values.

### 3.1 How the score is shown

`score` is a cosine similarity between TF-IDF vectors. Phase 4 sec 4 measured a
median top-1 cosine of 0.281, with 64.2% of queries below 0.30. It is therefore
rendered as:

```
Similarity 0.28 · 6 shared terms
```

and deliberately **not** as a percentage, a progress bar, or a star rating. Each
of those would imply a calibrated scale the number does not have: "28%" reads as
a probability or a grade, and a bar at 28% reads as "28% of the way to a good
match". `shared_terms` is shown beside it because Phase 4 sec 4 also shows that
evidence *count* is what separates a 1-term match from a 30-term one when
magnitude cannot. No test contains a `%` in a rendered result.

### 3.2 The evidence, and the skipped list

`/meta` and every recommendation response carry an `evidence` block
(`candidates_examined`, `candidates_rejected`, `returned`, `exhausted`,
`min_shared_terms`). The UI always shows the denominator: *"5 of 7 candidates
examined met the evidence threshold of 3 shared indexed terms; 2 rejected."* A
short result list presented without its denominator reads as a complete one.

The `skipped` array is **tallied, not listed**. One measured query returns 1,837
skipped candidates — 160 KB of JSON. Rendering that would cost a long scroll to
convey a fact `evidence` already states. The per-reason counts are derived from
the array the engine already sent (a tally, with no judgement applied) and
labelled as derived; when the tally is smaller than `candidates_rejected` the UI
says so rather than implying it is the whole picture.

---

## 4. Configuration

`src/config.ts` is the only module that reads `import.meta.env`, and
`.env.example` documents the one variable. The variable is named
`VITE_API_BASE_URL` because the framework is Vite; there is no
`NEXT_PUBLIC_` prefix in this project.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_BASE_URL` | unset (same-origin) | API origin, no trailing slash. |
| `BACKEND_ORIGIN` | `http://127.0.0.1:8000` | Vite dev-proxy target. Not shipped to the browser. |

**The default is same-origin, and that is the recommended setup.** With no base
URL the app requests `/api/v1/...` relative to its own origin, which the Vite
dev proxy forwards in development and a reverse proxy forwards in production.
This is not only ergonomic: the backend's CORS allowlist is four explicit
localhost origins and no wildcard (phase-05 sec 6), so a same-origin relative
path is what keeps the app working unchanged on a different port or behind a
proxy. `VITE_API_BASE_URL` is for the split-host case, where the API origin must
be on that allowlist.

Tests assert the relative form directly — `expect(calls[0]).toBe('/api/v1/health')`
— which is what makes a hard-coded `http://localhost:8000` in a component an
immediate failure rather than a runtime surprise. A regression is also guarded in
the client itself: `request()` builds the URL with a placeholder origin and must
use the *path* when no base is configured and the *full URL* when one is, since
`new URL` always returns an absolute URL and the origin would otherwise be
silently dropped.

Tunables that are not env vars are constants next to the values they relate to
(`SEARCH_DEBOUNCE_MS`, `SEARCH_MIN_LENGTH`, `DEFAULT_K`, `K_OPTIONS`,
`REQUEST_TIMEOUT_MS`), each with its reasoning in a comment.

---

## 5. User flow

1. **Search.** Type at least 2 characters. One character matches 1,700+ of the
   5,000 titles, so below two characters the app stays idle and the status line
   says why rather than spending a request.
2. **Results.** Title matches only, ordered by match tier. The tier is shown
   (`exact`, `title_prefix`, `original_title_prefix`, `title_contains`) because
   the ordering is structural, not a relevance score the user could otherwise
   infer. The status line reports the true total: *"Showing 4 of 1,701 matches."*
3. **Select a film.** Details load automatically, as do recommendations — a
   single indexed lookup measured at ~1.02 ms, so making the user click to see
   it would only add a step and a state to explain.
4. **Read the results.** Ranked, with score, shared terms, and the evidence
   summary. `k` can be set to 5, 10, or 20.
5. **Walk the catalogue.** Clicking a recommendation makes it the new query
   film, re-running the flow from a new position.
6. **Reset.** `Escape` in the search box clears both the query and the selection.

### 5.1 There is no plot summary

`MovieDetail` has no `overview` field, and the UI does not fabricate one. The
prose exists in the gitignored raw snapshot but not in `movies.jsonl`, which
stores the tokenised `document`/`tokens` that Phase 3 derives and Phase 4 pins by
hash. Obtaining a synopsis in the browser would mean either inventing plot text or
calling TMDb directly and shipping an API key to the client; both are worse than
an acknowledged gap.

So the details panel shows what genuinely exists — title, original title, year,
release date, runtime, language, genres, collection, vote, `document_size` — plus
a one-line note that the summary is unavailable by design. A mysterious blank
space reads as a bug; an acknowledged limitation does not. The `feature_counts`
breakdown is shown in its place, because it is the honest answer to "what is this
app actually comparing here": it is the composition of the indexed text the score
is computed from.

Sentinel values are treated as absent rather than shown. `runtime_minutes: 0` is
TMDb's "not recorded", so it renders as `—`; a `vote_average` of 0.0 with zero
votes is the unrated sentinel, so it renders as `—` rather than "0.0 / 10",
which would read as "audiences rated this zero".

### 5.2 An empty result is information

Zero recommendations is not a failure. Two distinct causes are separated, because
they are different problems: `candidates_examined === 0` means nothing in the
catalogue was comparable at all, while a large rejected count means plenty was
comparable and nothing cleared the evidence threshold. The panel explains which
happened and how many candidates were examined. The 422 `insufficient_features`
case — a film with almost no indexed text, which is not recommendable as a query
at all — arrives as an error and is shown with the backend's own wording.

---

## 6. Accessibility

- **Landmarks and headings.** `header`/`main`/`footer`, one `h1`, and each panel
  is a labelled `section` with a correctly levelled heading.
- **Skip link.** First focusable element. The DOM order is search → details →
  results, so without it a keyboard user would tab through the entire result list
  on every page to reach the recommendations they just asked for.
- **Search is a combobox over a listbox.** `ArrowDown` from the input focuses the
  first result and moves down through them; `ArrowUp` from the first result
  returns to the input rather than trapping; `Enter` selects; `Escape` clears.
  Options are real `<button>`s so focus is real focus and the browser's own
  activation behaviour still works.
- **Announcements.** Loading, result counts, and empty states go through a polite
  live region. Errors use `role="alert"`, which is assertive, because a failure is
  the one thing that should interrupt. Both are asserted in tests.
- **Focus is visible everywhere,** set once at the base layer with
  `:focus-visible`, so a custom ring on buttons cannot leave a default outline
  somewhere that is nearly invisible on a dark background.
- **Nothing is conveyed by colour alone.** The selected film is distinguished by
  accent colour *and* `aria-selected` *and* border weight. Skeletons are
  `aria-hidden` — the loading state is announced by the live region instead, so a
  screen reader is not read eight anonymous grey bars.
- **Motion.** One 150 ms colour transition and one 120 ms fade.
  `prefers-reduced-motion: reduce` removes both, and stops the skeleton shimmer.
- **Contrast.** Body text is #f5f5f4 on #0b0b0d (~18:1), muted text #a8a29e
  (~7:1), accent #f0b429 (~9:1) — all past WCAG AA for body text.

## 7. Visual design

Dark, low-chroma, with a single accent. The only saturated colour on screen is the
amber accent, and it is reserved for the *selected* film. That is functional: it
is how the user tells "the film I picked" from "films it matched" without reading
a label. Because colour is doing that work, the same distinction is repeated in
text and border weight so it survives for a user who cannot perceive the hue.

Layout is one column on mobile and a `5fr / 7fr` two-column grid at `lg`. The DOM
order is task order (search → details → results) and the wide-screen arrangement is
a visual reordering only, so tab order always follows what the user is doing and
neither column scrolls independently. System font stack only — no web font, no
CDN, no external request of any kind.

---

## 8. Licensing and attribution

`GET /meta` returns the attribution block, and the UI renders
`attribution.notice` **verbatim** — not restyled, not reworded, not truncated:

> This product uses TMDB and the TMDB APIs but is not endorsed, certified, or
> otherwise approved by TMDB.

The text is fetched rather than hard-coded so it travels with the backend and
cannot drift from it. The backend's `test_tmdb_notice_is_verbatim` asserts it
there; `app.test.tsx` asserts the same sentence against a captured fixture, so a
careless edit here fails a front-end test.

A byte-identical fallback constant in `useAbout.ts` exists for the one case the
fetch cannot cover: the API is unreachable, and the user would otherwise see an
About panel with no attribution at all — the worst possible moment to fail a
licence requirement. A test covers that path too.

No TMDb credential or API call appears anywhere in the browser. `source` and
`license` from the same block are shown beneath the notice, and the engine's own
`notes` — the Phase 4 caveats about what the similarity score is and is not — are
displayed in the About panel rather than hidden. Burying them would be the
dishonest choice.

---

## 9. Testing

69 tests, all passing, in two files.

`src/test/client.test.ts` (32 tests) — URL construction and encoding, including
that a same-origin path has no origin, that `½` survives UTF-8 percent-encoding,
and that `k` and `limit` are sent only when supplied; response decoding against
real payloads; error mapping for 404, 422, 503, a non-JSON error body, a dead
backend, and a 2xx body of the wrong shape; that a blank query is refused without
a round trip; abort detection for both `DOMException` and undici's plain
`Error`; and the timeout translating to a network error.

`src/test/app.test.tsx` (37 tests) — initial render and the idle states; that no
request is made before typing; the debounce and the minimum length; results with
the true total; the empty state; search errors from both a 4xx envelope and a dead
backend; the stale-response guard; selection, details, and the 404 path; the
missing-overview note; a non-recommendable film; recommendations requested for the
selected film and rendered in engine order; the score shown as a score with no
`%`; the evidence denominator; skipped candidates tallied not listed; the empty
result explained; `k` re-requesting; the 422 path; a recommendation becoming the
new query film; and the keyboard model, skip link, live region, and `aria-pressed`
state.

### 9.1 Fixtures

Responses captured from a running Phase 5 backend, not hand-written, so the tests
assert against shapes the backend actually produces. Thirteen files in
`src/test/fixtures/`, including the awkward real cases: a null-title film, a
Unicode title (`The Naked Gun 2½: The Smell of Fear`), a 4-result search that
reports 1,701 total matches, and a recommendation response whose `skipped` array
carries 1,837 entries against `evidence.candidates_examined` of 1,842.

Two fixtures are trimmed, and the trimming is declared in the response rather than
hidden: `recommendations_short` keeps 5 of its results and 3 of its 1,837 skipped
entries, and `recommendations_none` keeps 2 of its 577. `evidence` is untouched in
both, so the totals under test are the real ones, and `app.test.tsx` asserts the
"1,842" figure specifically. `EvidenceSummary` additionally renders a note when the
derived tally is smaller than `candidates_rejected`, so a trimmed fixture is
labelled as incomplete rather than passing as a whole response.

`fetch` is stubbed per test by `src/test/mockApi.ts`, which routes by path, returns
the documented error envelope for failures, and reproduces three real `fetch`
behaviours the tests depend on: rejecting an already-aborted signal, allowing a
delayed response to arrive after the caller gave up, and rejecting a dead backend
with `TypeError`. An unlisted path returns a 404 envelope rather than hanging, so
"the client called the wrong URL" is an obvious failure rather than a timeout.

---

## 10. Verification

| Gate | Command | Result |
| --- | --- | --- |
| Types | `npm run typecheck` | clean |
| Lint | `npm run lint` | clean, 0 errors 0 warnings |
| Tests | `npm test` | 69 passed |
| Build | `npm run build` | 43 modules, 250 kB / 78 kB gz |
| Backend regression | `python -m pytest -q` | 331 passed |
| Backend lint | `python -m ruff check .` | all checks passed |
| Backend types | `python -m mypy moviemind` | no issues, 15 files |

Live end-to-end check against a running backend through the Vite dev proxy:

```
GET /                          200, serves #root
GET /api/v1/meta               v1, fingerprint af683a71bb38
GET /api/v1/movies/search      q=shawshank -> 1 of 1, 'The Shawshank Redemption', match=title_contains
GET /api/v1/movies/278         'The Shawshank Redemption' (1994), doc_size 75, no 'overview' field
GET /api/v1/movies/278/recs    k=5 -> 5 returned of 7 examined, 2 rejected, ranks 1-5 in order
```

The live response confirms the two claims this document leans on: the details
payload has no `overview` field, and the ranking arrives intact.

**No backend file was modified in this phase.** `data/processed/` is unchanged
and the Phase 4 config fingerprint served at `/meta` is still
`af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7`.

---

## 11. Limitations and what was deliberately not done

- **No plot summaries or images**, for the reasons in sec 5.1. Posters and
  backdrops do not exist anywhere in the project: Phase 2 dropped all image
  fields. The UI is metadata-first and does not substitute decorative artwork,
  which would imply a poster exists for each film.
- **Search is title-only**, matching the API. There is no cast, genre, or
  keyword search, and no year filter, because the backend exposes neither.
- **No routing or deep links.** The selection is component state, so a reload
  loses it and there is no shareable URL. Acceptable for a single-user local tool;
  a router would be the change to make if that stops being true.
- **No offline or cached results.** Every selection re-requests. For a measured
  1 ms local lookup that is the right trade, and it means the UI cannot show data
  that disagrees with the engine.
- **No accessibility testing with a real screen reader.** The semantics, roles,
  and live regions are asserted in jsdom, which verifies the contract but not the
  announcement. Manual testing with NVDA or VoiceOver is still worth doing.
- **Contrast ratios are computed, not measured** with an automated tool, and no
  colour-blindness simulation was run. The palette does not rely on hue alone, but
  this is not a substitute for testing.
- **A `config_fingerprint` naming collision exists in the repository**, outside
  this phase's scope and unchanged by it. `data/processed/build_manifest.json`
  records `4615a843…`, the Phase 3 feature/preprocessing config, while `/meta`
  publishes `af683a71…`, the Phase 4 recommendation config. Both are correct for
  what they measure; the front-end displays the `/meta` value, which is the one
  Phase 4 pins. Worth a note in the phase docs so a future reader does not
  mistake one for drift in the other.
- **The `.env.example` documents a cross-origin setup that was not exercised
  end to end**, because the default same-origin path is the supported local
  configuration. The absolute-URL branch in `request()` is unit-tested against a
  stubbed `fetch`, but has not been run against a real split-origin deployment.

## 12. Running it

```bash
# terminal 1 - the API (loads the catalogue, ~0.3 s)
python -m uvicorn moviemind.api.app:app --port 8000

# terminal 2 - the front-end
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000
```

`npm run build` emits a static `dist/`. Serve it from any static host, and set
`VITE_API_BASE_URL` only if the API is on a different host (sec 4).
