# Phase 3 — Feature Engineering

**Status:** complete. Phase 4 has not started.
**Scope:** raw TMDb snapshot → deterministic, Unicode-safe feature documents → candidate TF-IDF representations A/B/C/D, built and measured.

| Artifact | Value |
| --- | --- |
| Raw input | `data/raw/tmdb_movies.jsonl` |
| Raw SHA-256 | `5b1079cd46efad0dec21f74736bebc6db6ba2f91e1b7497365c86225fd36307b` (unchanged) |
| Config fingerprint | `4615a84314f53e5fa5114616bc4e95314c96a6568b455f6c1413fed0ec42ccd8` |
| Processed corpus | `data/processed/movies.jsonl` |
| Corpus SHA-256 | `53b0b8c675651ea438230fe75bba3660f52213428876f9cb99e565825fcdece0` |
| Films | 5,000 (0 malformed, 0 invalid ID, 0 duplicate ID) |
| Tokens | 261,037 (mean 52.21/film) |
| Tests | 124 passing |

---

## 1. What was built

```
moviemind/config.py          every decision, with its measured justification, hashed into a fingerprint
moviemind/text.py            normalisation, tokenisation, phrase canonicalisation
moviemind/pipeline.py        raw -> feature documents, defensive loading
moviemind/representations.py candidates A/B/C/D, per_block and flat combination
scripts/build_features.py    the build
scripts/compare_representations.py   the A/B/C/D measurement, both block modes
tests/                       124 tests
```

Reproduce:

```powershell
python scripts\build_features.py
python scripts\compare_representations.py --block-mode per_block --label per_block
python scripts\compare_representations.py --block-mode flat     --label flat
python -m pytest tests -q
```

The build is byte-for-byte reproducible: two runs produce identical corpus
SHA-256. Timestamps live only in the manifest, never in the corpus file, so the
corpus can be diffed and hash-compared across runs.

---

## 2. Decisions and the evidence for them

### 2.1 NFC, not NFKC

NFC alters **0** strings in the snapshot. NFKC alters **87** and destroys
meaning: `The Naked Gun 2½` becomes `The Naked Gun 21⁄2`. Compatibility folding
has no upside here and a real one to lose.

### 2.2 Diacritics preserved

`strip_accents=False`. 13.3% of overviews and 14.5% of original titles contain
non-ASCII characters, overwhelmingly accented Latin. Folding would merge
`Amelie` and `Amélie` while adding no retrievable signal.

### 2.3 Non-Latin scripts

CJK, Cyrillic, Greek, Georgian and Korean tokens survive intact. Scripts
without inter-word spaces produce one token per run (`板山之秋`), which is
correct — there is no separator to break on. The snapshot contains only 6 CJK
characters in total, so this is a correctness guarantee rather than a
volume-driven one.

### 2.4 Digits kept

88 keyword types contain digits. Era descriptors (`1970s`, `19th century`,
`1600s`) are genuine signals, so a "drop numeric tokens" rule was rejected.

### 2.5 Namespaces

Every token carries a field prefix (`ov:`, `kw:`, `gn:`, `cast:`, `dir:`).

| Intersection | Shared surface forms |
| --- | --- |
| overview ∩ cast | 4,226 (`aaron`, `abby`, …) |
| overview ∩ keywords | 5,708 |
| overview ∩ genres | 21 (all of them) |

Unprefixed the union is 56,889 terms; namespaced, 69,730 (+23%). The 23% buys
field isolation and per-field `min_df`/weighting, which is the main quality
lever available.

### 2.6 Keyword `min_df=2` — provably lossless

A keyword appearing in exactly one film has a single non-zero in its column and
therefore **cannot contribute to any off-diagonal cosine**. Pruning it changes
no similarity between any pair of films.

| `min_df` | types kept | occurrences kept |
| --- | --- | --- |
| 1 | 11,914 | 47,340 |
| **2** | **5,572** | **40,998 (86.6%)** |
| 3 | 3,572 | ~78% |
| 5 | 2,005 | ~67% |
| 10 | 922 | ~52% |

The snapshot contains no keyword that is 0% for 2 films and later becomes
useful, so `min_df=3+` is pure precision loss. `min_df=2` is the free lunch.

### 2.7 Keywords and genres are atomic phrases

`post-apocalyptic future`, `post_apocalyptic future` and `Post Apocalyptic
Future` all collapse to `kw:post_apocalyptic_future`. Splitting keywords into
words was measured and rejected: it adds ~27% features that are largely
redundant with the overview block.

Genre labels stay whole for the same reason: `gn:science_fiction` is one
feature, and the genre vocabulary is exactly the 19 controlled labels.

### 2.8 Credits-stinger keywords removed

`duringcreditsstinger`, `aftercreditsstinger`, `beforecreditsstinger` describe
post-roll content, not the film. 275 films (5.5%) carry one, and they would
make unrelated films look alike. Removed in the pipeline rather than the
vectoriser, so they are absent from the processed documents and remain
auditable per film via `stinger_keywords_removed`.

### 2.9 Conservative English function words — and a real bug

sklearn's `stop_words="english"` (318 terms) carries **14.51%** of the overview
TF-IDF mass, but it deletes words that distinguish films:

```
after(820) up(649) one(631) out(582) two(473) all(447) find(397)
must(393) this(374) will(374) only(353) can(294) get(285) …
```

`find`, `after`, `one`, `only`, `during`, `must` are exactly the words that
separate one plot from another. The project's list is 88 closed-class terms
only — articles, prepositions, auxiliaries, conjunctions, and pronouns.
Personal pronouns matter: `his` (df 3,749), `her` (2,104), `he` (2,061),
`they` (1,069) are among the most frequent tokens in the corpus and carry no
film-specific information.

Result: **36.2% of overview tokens removed, 8.28% of TF-IDF mass**, i.e. 57% of
the removable mass while keeping every content-bearing word.

> **Bug found and fixed during this phase.** Stopword removal was configured on
> the vectoriser via `stop_words="english"`, which matches whole strings. Every
> token is namespaced (`ov:the`), so the option matched **nothing** and the
> configured filtering was a silent no-op. The list is now applied upstream in
> `extract_overview_tokens`, on the unprefixed token, and the vectoriser-level
> option is gone so there is a single source of truth. Overview tokens fell
> 219,755 → 140,197 once this was actually working.

### 2.10 Cast truncated to the top 5 billed

Billing order is trustworthy: all 5,000/5,000 cast lists arrive already sorted
ascending by `order`, so "first K" genuinely means top-billed. The audit found
no junk-credit pattern, and the snapshot's own cap is 20.

The truncation target is "hold cast below a fifth of the document". That target
has to be measured on the tokens that actually reach the vectoriser:

| `top_k` | tokens/film | cast | overview | keywords | genres | director |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | 48.22 | 12.67% | 58.15% | 19.49% | 5.11% | 4.58% |
| **5** | **52.21** | **19.34%** | **53.71%** | **18.00%** | **4.72%** | **4.23%** |
| 8 | 58.03 | 27.44% | 48.32% | 16.20% | 4.25% | 3.80% |
| 10 | 61.77 | 31.82% | 45.40% | 15.22% | 3.99% | 3.57% |
| 20 | 77.93 | 45.96% | 35.98% | 12.06% | 3.16% | 2.83% |

`top_k=10` was originally chosen from a table measured on **raw** whitespace
tokens, before the overview had its function words removed. Removing them
shrank the overview denominator and quietly made cast (31.8%) larger than
keywords (15.2%), invalidating the stated rationale. `top_k=5` is the value that
actually satisfies it: overview largest, keywords second, cast supporting.

Cast *coverage* is unaffected — a film with one credited actor contributes cast
tokens at any `top_k`. Only the vocabulary shrinks, and the shrunk tail is
mostly one-off names that `min_df=2` removes anyway.

### 2.11 Directors are a set

285 films are legitimately co-directed (Brave: Mark Andrews and Brenda
Chapman). A scalar director field would silently drop one director for each.
`top_k=3` is above the observed maximum, so nothing is lost.

### 2.12 Ratings never enter the vector

`popularity`, `vote_average` and `vote_count` are carried through to the
processed records for display and later re-ranking, and are never tokenised.
`collection_name` likewise. Phase 1 §5.2 and Phase 2 §12.

---

## 3. Bugs found and fixed

Recorded because each was silent — the build ran clean and produced plausible
numbers throughout.

1. **Stopwords were a no-op.** sklearn's whole-string matching cannot match
   `ov:the`. Detailed in §2.9.
2. **Multi-word phrases were being shredded.** Phrase tokens contained spaces
   (`gn:science fiction`) and the vectoriser splits on whitespace, so
   `Science Fiction` became `gn:science` plus a bare, *unnamespaced* `fiction`.
   The genre vocabulary came out as 21 features instead of 19, and unnamespaced
   terms leaked into the vocabulary. Fixed by joining phrase internals with `_`.
3. **Flat mode used the field name as the namespace prefix.** The configured
   prefixes are `ov`/`kw`/`gn`/`cast`/`dir`, which are not the field names, so
   only `cast` (where they coincide) resolved. Every other field reported 0
   dimensions and 0% contribution.
4. **Flat mode took the loosest `min_df`.** A single vectoriser needs one
   `min_df`; taking the *minimum* across enabled fields meant genre's
   `min_df=1` governed the whole vocabulary, re-admitting singletons. The
   strictest (maximum) value is used instead, and with this dataset nothing is
   lost because all 19 genres appear in 96+ films.
5. **Field attribution was computed by contiguous column offsets.** Valid for
   per-block concatenation, wrong for flat mode, whose vocabulary is sorted
   alphabetically and therefore interleaves fields. Flat-mode contribution
   shares and coverage were reporting arbitrary column mixtures. Blocks now
   record their actual column indices. Cross-check: the two modes now report
   identical per-field coverage (4,913 / 4,944 / 4,493 / 4,933 / 4,511).

---

## 4. Candidate representations

All four candidates, **both block modes**. Dimensions, nonzeros and density are
identical across modes by construction; only the *field weighting* differs.

| | A: overview | B: +genres | C: +keywords | D: +cast+director |
| --- | --- | --- | --- | --- |
| dimensions | 10,489 | 10,508 | 16,078 | **23,404** |
| nonzeros | 121,394 | 133,721 | 174,380 | 226,352 |
| density | 0.231% | 0.255% | 0.217% | **0.193%** |
| mean nnz/doc | 24.28 | 26.74 | 34.88 | 45.27 |
| all-zero films | 87 | 21 | 18 | **0** |

Per-block dimensions (D): overview 10,489 · genres 19 · keywords 5,570 ·
cast 5,688 · director 1,638.

Documents carrying each field: overview 4,913 · genres 4,944 · keywords 4,493 ·
cast 4,933 · director 4,511.

**D is the only candidate that represents all 5,000 films.** A leaves 87 films
with no vector at all, B leaves 21, C leaves 18. Those are the films with no
usable synopsis — they would be unreachable by any content-based method, and
they are exactly the long-tail films a recommender is supposed to help with.

---

## 5. The central finding: how fields are combined matters more than which fields

This is the decision Phase 3 could not make on evidence alone, so both modes
are measured and neither is declared the winner.

Field contribution = share of total squared L2 mass, i.e. how much of a
document's vector energy that field actually controls.

| | overview | genres | keywords | cast | director |
| --- | --- | --- | --- | --- | --- |
| **per_block B** | 49.69% | **50.31%** | – | – | – |
| **flat B** | 96.19% | 3.81% | – | – | – |
| **per_block C** | 34.65% | 35.20% | 30.15% | – | – |
| **flat C** | 70.92% | 2.89% | 26.19% | – | – |
| **per_block D** | 20.80% | 21.11% | 18.38% | 21.06% | 18.65% |
| **flat D** | 50.71% | 1.33% | 18.70% | 23.86% | 5.40% |

The tension, in representation B, is stark and instructive:

- **per_block** normalises each field alone, so the 19-dimension genre block
  receives exactly as much energy as the 10,489-dimension overview block —
  **50.31% of the vector for a field whose IDF is ≈0.01** (every genre appears
  in 4,000+ films). Equal weighting hands control of the vector to the least
  informative field present.
- **flat** concatenates and vectorises once, so a field follows its token
  share and IDF naturally suppresses ubiquitous terms. But this over-corrects:
  genres fall to 1.33%, and a genre match contributes almost nothing to
  similarity.

Neither is right by default. per_block gives exact control; flat gives a
sane prior. The two failure modes are opposite, which is the strongest argument
for keeping `per_block` as the substrate and setting weights explicitly.

### Weight solver

Field influence scales with `weight²`. To move a field from its current share
`s₀` to a target share `s`:

```
weight *= sqrt( (s / (1 - s)) * ((1 - s₀) / s₀) )
```

Starting from the measured per_block D shares:

| field | current | target | implied weight |
| --- | --- | --- | --- |
| genres | 21.11% | 5% | 0.443 |
| genres | 21.11% | 3% | 0.340 |
| director | 18.65% | 8% | 0.616 |
| director | 18.65% | 5% | 0.474 |
| cast | 21.06% | 15% | 0.827 |
| cast | 21.06% | 10% | 0.657 |
| keywords | 18.38% | 25% | 1.197 |
| keywords | 18.38% | 30% | 1.352 |

These are *starting points for evaluation, not a recommendation.* No weight in
this table has been validated against relevance, because validating it requires
the Phase 4 evaluation, and that evaluation is the thing this phase is meant
to inform. Shipping an unvalidated weight would be exactly the intuition-based
choice this phase is required to avoid.

---

## 6. Recommendation for Phase 4

1. **Representation D**, all five fields. It is the only candidate with total
   coverage, and the extra fields cost 12,915 dimensions and roughly double
   mean nnz/doc while keeping density at 0.193%.
2. **per_block** combination, with explicit weights. It is the only mode in
   which field importance is a stated decision rather than a side effect of
   token counts and IDF.
3. **The first thing Phase 4 must measure is the genre weight.** A genre block
   holding 50% of the vector (per_block B) or 1.3% (flat D) are both
   indefensible without evidence, and genres are the field whose weight is
   least constrained by the data.
4. **Build the evaluation harness before tuning anything.** Phase 4 needs:
   - a held-out protocol that does not reward popularity (rank correlation
     against `popularity` and `vote_average` is a *diagnostic*, and a high
     value means the metric is measuring popularity, not content);
   - a trivial-similarity baseline (genre-only, and most-popular) so that any
     claimed gain is measured against something;
   - explicit handling of the 69 same-title remake/re-release groups, which are
     a natural ground-truth "should be near each other" signal that is free and
     not popularity-driven;
   - cast/director recurrence as a second free signal, given that only 27.0%
     of people recur.
5. **Do not** retune `min_df`, the stopword list, `top_k`, or NFC on the basis
   of Phase 4 relevance numbers without re-running this phase's losslessness
   argument for any of them. The `min_df=2` result in particular is a proof,
   not a hyperparameter.

---

## 7. Licensing

Unchanged from Phase 1 and Phase 2, and enforced in the build manifest.

- TMDb content is used for **non-commercial retrieval and similarity only**.
- **Attribution is mandatory** in any UI; the exact required wording is carried
  in `build_manifest.json` under `licence.attribution_required`.
- The data must **not** be redistributed, and must **not** be used to train a
  model.
- Refresh within six months of the snapshot date.
- `data/raw/`, `data/audit/` and `data/processed/` are git-ignored. Processed
  tokens are derived from TMDb content and are not committable either.

---

## 8. Test coverage

124 tests, all passing. `tests/test_snapshot_integration.py` is skipped
automatically when the raw snapshot is absent, so the suite runs on a fresh
clone.

| File | Covers |
| --- | --- |
| `test_text_unicode.py` | NFC vs NFKC, `2½` preservation, composed/decomposed equivalence, diacritic retention, CJK/Cyrillic/Greek/Georgian/Korean, digits and era tokens, phrase canonicalisation, conservative-vs-sklearn stopword behaviour, hostile inputs |
| `test_pipeline_fields.py` | per-field extraction, billing-order sort (including that it reorders unsorted input), `top_k` enforcement, co-directors, duplicate names, stinger removal, stub overviews, ratings/collection never tokenised |
| `test_pipeline_integrity.py` | duplicate IDs keep first occurrence and are reported, same-title different-ID films both kept, malformed/invalid/blank lines, byte-identical rebuilds, no timestamp in the corpus, raw input never modified, source record not mutated, literal Unicode in output, fingerprint sensitivity |
| `test_representations.py` | L2 normalisation, shape consistency, empty vocabulary, `min_df` pruning, no unnamespaced leakage, phrase integrity, both block modes, strictest-`min_df` rule, unknown mode rejected, monotonic coverage, JSON-serialisable output |
| `test_snapshot_integration.py` | raw hash unchanged, unique IDs, no surviving stingers, namespacing, 19 genres, token-share guardrails, D total coverage, A matches the no-overview count, manifest provenance |
