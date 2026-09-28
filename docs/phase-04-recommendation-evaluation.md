# Phase 4 — Content-Based Recommendation and Evaluation

**Status:** complete. One configuration selected, measured end to end, and reproducible byte-for-byte.

**Scope.** This phase builds a recommendation engine over the Phase 3
representation and evaluates it honestly given what the data can support. It
does not add a backend, frontend, authentication or database. It does not train
a predictive model. Every number below comes from
`python scripts/run_experiments.py` and is recorded in
`data/processed/experiments.json`.

---

## 1. Headline

| | |
|---|---|
| **Selected configuration** | representation D, `per_block`, `genres.weight = 0.44`, `min_shared_terms = 3`, `cast.top_k = 5`, `k = 10` |
| **Config fingerprint** | `af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7` |
| **Geometry** | 23,404 dimensions, 226,352 nnz, 0.1934% density |
| **Field energy** | overview 25.14%, keywords 22.02%, cast 25.47%, director 22.31%, **genres 5.07%** |
| **Franchise recall@10 / hit / NDCG** | 0.860 / 0.909 / 0.774 (n=528) |
| **Director recall@10 / hit / NDCG** | 0.985 / 0.997 / 0.978 (n=3,125) |
| **Cast recall@10 / hit / NDCG** | 0.157 / 0.605 / 0.413 (n=4,498) |
| **Same-title recall@10** | 0.171 (n=140) — weak label, see §3.4 |
| **Catalogue coverage@10** | 0.986 |
| **Popularity bias** | Spearman 0.051; mean recommended popularity percentile 52.4 (neutral is 50) |
| **Query latency** | 2.4 ms mean over 400 warm queries at `k = 10`, `min_shared_terms = 3` (1.5 ms before the §9 backfill fix); no index build |
| **Tests** | 222 passing (206 at Phase 4 close, 16 added with the backfill fix) |

The single most important finding is negative and structural: **there is no
relevance ground truth in this dataset, and no independent label exists for the
one decision that mattered most.** Section 3 explains why, and section 7 explains
how the genre weight was chosen without pretending it was validated.

---

## 2. What was built

### 2.1 Engine — `moviemind/recommend.py`

`Recommender` wraps one representation and answers top-K queries.

* **Sparse throughout.** A query is one sparse mat-vec, `X @ q`. An `N x N`
  matrix is never materialised. Batched offline scoring fetches `batch x N`
  dense scores, bounded to 2^22 elements (~5 MB) per step, and reduces to
  `batch x k` immediately.
* **Cosine for free.** Each block is L2-normalised and the concatenation is
  re-normalised, so cosine *is* the dot product.
* **Deterministic ranking.** Sort key is `(-score, movie_id)`. This is not
  cosmetic: 36.7% of the scores in representation A's top-50 candidate pool are
  bit-identical duplicates, so an unstable sort would emit a different top-K on a
  different machine or NumPy build. Verified by
  `test_sparse_top_k_breaks_ties_by_ascending_index` and by byte-identical
  repeat runs.
* **Explicit failure modes.** `UnknownMovieError` (id not in catalogue) and
  `InsufficientFeaturesError` (all-zero vector) are distinct types, because one
  is a caller error and the other is a data condition the product must explain.
  Candidates dropped by the evidence filter are *returned* in
  `RecommendationResult.skipped` with a reason, never silently discarded.
* **The evidence filter can promote, not only delete.** `recommend()` ranks the
  full candidate set and walks it in order, filtering as it goes and stopping at
  `k` accepts, so a rejected high-scoring row is replaced by a passing lower one.
  It returns exactly `k` results whenever the catalogue can supply `k` valid
  candidates, and fewer only on genuine exhaustion. The pre-fix engine did not do
  this; §9 records the defect, the fix, and the measured impact.

### 2.2 Evaluation — `moviemind/evaluate.py`

Per-label recall / hit-rate / NDCG, plus label-free coverage, intra-list
diversity, distinct genres, distinct decades, mean top-1 score, thin-evidence
rate, and two popularity diagnostics. Every metric ships with a
`MetricDefinition` carrying an explicit `does_not_measure` string, and those
definitions are written into the results artifact, so a metric cannot be quoted
without its caveat travelling with it.

### 2.3 Experiments — `moviemind/experiments.py`

33 experiments in 7 families, all under one protocol, all recorded with full
provenance (config fingerprint, field weights, `min_df`, `cast.top_k`,
`block_mode`, `min_shared_terms`, geometry, field shares).

---

## 3. Why the evaluation is shaped this way

### 3.1 There is no ground truth

No users, no watch history, no interactions, no per-user ratings, no human
judgements. Every label is a *structural proxy* derived from TMDb's own
editorial metadata. None of them is user satisfaction. This is stated in the
module docstring of `moviemind/labels.py` and repeated in the artifact's `note`
field.

### 3.2 Labels come from raw, and that is not optional

Person identity is only recoverable from the raw snapshot. The processed corpus
stores `tokens['cast']` as a flat, de-duplicated, order-preserving union of name
tokens with no person boundaries, so `cast:tom cast:hardy cast:elliot cast:page`
is indistinguishable from a single four-token surname. `ProxyLabels.from_raw`
raises `MissingCreditsError` on processed records rather than inventing one long
fake actor per film.

Names are matched on the **full normalised name**, not on the name tokens Phase 3
stores per film, because the processed corpus keeps one flat token set per field
and cannot say which token belonged to which person. Grouping directors by their
full-name token set reproduces the 1,030 real recurring directors exactly — token
collisions are not a practical risk — but that is only true because the *whole*
name is used; matching a single token (`dir:john`) would collapse every director
called John into one group.

`cast.top_k` in the label builder must equal the vector's own bound, or a
representation gets credited for information it never had.

### 3.3 Label inventory

| Label | Groups ≥2 | Films with a relevant film | Mean relevant | Character |
|---|---|---|---|---|
| `franchise` | 215 | 528 | 1.88 | **Strongest.** TMDb asserting one series. |
| `director` | 1,030 | 3,125 | 3.27 | Strong, and the only one conceptually independent of genre. |
| `cast` | 3,867 | 4,498 | 19.15 | Weak per-film: groups are huge, so recall is bounded low by construction. |
| `same_title` | 69 | 140 | 1.04 | **Weakest.** Mixed remakes and coincidences. |

Counts come from `scripts/build_evaluation_labels.py` and match the Phase 4
ground-truth investigation exactly. Label artifact SHA-256:
`7717c4283a58f58b…`.

### 3.4 Same-title is not a remake signal

The Phase 3 brief suggested treating all 69 same-title groups as remakes.
Measured, that is only partly true. `beauty and the beast`, `conan the
barbarian`, `fright night` and `friday the 13th` are genuine remakes; `anna`,
`house`, `havoc`, `fair game` and `get carter` are coincidences between
unrelated films. 15.1% of same-title pairs share no genre at all, versus 0.6%
for franchise pairs. It is reported as a measurement of a noisy label, never as
a system failure.

### 3.5 The circularity that shapes everything

Within-franchise genre Jaccard is **0.775** (n=990 pairs, only 0.6% sharing no
genre); random film pairs are **0.147**, with **56.0%** sharing no genre at all
(200,000 sampled pairs, seed 7).

So genre-overlap "relevance" is largely a restatement of genre agreement. The
mandatory genre-weight experiment therefore **cannot be validated by any signal
in this dataset.** This is stated up front rather than discovered later, and it
is why section 7 argues the decision rather than reading it off a number.

### 3.6 Unevaluable queries are counted, not scored as zero

4,472 of 5,000 films have no franchise sibling, 4,860 have no same-title peer.
Scoring those as misses would make every configuration look catastrophic and
would reward configurations that happen to surface rare films. Each label is
scored only over its own evaluable queries, and the excluded count is reported
per label.

### 3.7 No train/test split, and why that is correct

The system has no fitted parameters. Weights are set by hand from measured field
contributions. Nothing is learned from the data, so there is nothing to hold
out. The protocol is transductive full-catalogue leave-one-out: every film is a
query, the query is excluded from its own list, and the unit of evaluation is
the individual film. `seed` is recorded as `null` in the manifest, not
fabricated, because the protocol is fully deterministic — no sampling, no
shuffling, id-based tie-breaks.

---

## 4. Metric definitions

Every metric below travels with an explicit statement of what it does not
measure. Full text in `data/processed/experiments.json → metric_definitions`.

| Metric | Measures | Does **not** measure |
|---|---|---|
| `label_recall@K` | Proxy-group members retrieved | User satisfaction; undefined for the ~90% of films with no group |
| `label_hit_rate@K` | Whether a label-bearing query finds anything | Recall magnitude (a 1-relevant and a 9-relevant query both count as one hit) |
| `label_ndcg@K` | Position-aware proxy ranking | Anything recall does not; inherits every label limitation |
| `catalogue_coverage@K` | How much of the catalogue is reachable at all | Quality |
| `intra_list_diversity@K` | Whether a list repeats itself | Absolute diversity — computed in the tuned space, so within-configuration only |
| `distinct_genres@K` | Genre breadth of a list, weight-free | Quality |
| `distinct_decades@K` | Era breadth, weight-free | Quality |
| `mean_top1_score` | How tightly the catalogue clusters | Quality — scores are not calibrated across configurations |
| `popularity_spearman` | Whether the system is popularity-biased | Quality. **Diagnostic only, never optimised** |
| `mean_recommendation_popularity_pct` | Absolute novelty tilt | Quality |
| `thin_evidence_rate@K` | How often a hit rests on almost no evidence | Quality directly, but bounds how much of any score can be trusted |

**`thin_evidence_rate` is the metric that earned its place.** At Phase 3 defaults
49.3% of top-10 recommendations rest on 3 or fewer shared non-zero terms. An
absolute cosine threshold cannot detect this, because top-1 cosines are low
across the board (median 0.281; 64.2% of queries below 0.30) *and* unreliable at
the top: 9.0% of queries have a top-1 match sharing exactly **one** term, and
those pairs reach cosine 1.00 (films whose entire vector is a single shared
token). A pair with one term of evidence and a pair with thirty can score
identically. Evidence *count* separates these; evidence *magnitude* cannot.

**Popularity and ratings are diagnostics only.** The snapshot is already
popularity-skewed (median 5.29, max 99.01), and 126 films have zero votes.
Neither is independent relevance truth. Neither is optimised.

---

## 5. Results

### 5.1 Representation comparison (the required overview-only baseline)

Recall@10 / hit-rate@10 / NDCG@10, `min_shared_terms = 1`:

| | franchise | director | cast | same-title | cov | ILD | distinct genres | thin | top-1 |
|---|---|---|---|---|---|---|---|---|---|
| **A** overview (baseline) | .539/.634/.506 | .036/.069/.047 | .022/.151/.102 | .357/.357/.294 | .982 | .971 | **10.45** | .749 | .177 |
| **B** +genres | .554/.631/.491 | .049/.090/.055 | .024/.164/.102 | .300/.300/.200 | .940 | .448 | 3.16 | .543 | .598 |
| **C** +keywords | .758/.812/.641 | .062/.104/.068 | .032/.181/.113 | .332/.336/.212 | .915 | .556 | 3.77 | .592 | .499 |
| **D** +cast/director | **.903/.938/.819** | **.754/.902/.756** | .106/.534/.354 | .271/.271/.150 | .993 | .772 | 5.22 | .493 | .372 |

The hypothesised ordering D ≥ C ≥ B > A holds on franchise and director
recall. A is not useless — it is the most *diverse* configuration by a wide
margin (10.45 distinct genres per list versus D's 5.22) — but it is
substantially less related: it retrieves a director's other films 3.6% of the
time against D's 75.4%.

Two internal consistency checks pass: `only_overview` reproduces `rep_A`
exactly, and `drop_genres` reproduces `genre_w0` exactly.

### 5.2 Genre weight sweep (mandatory)

Weight → measured genre energy, on D:

| weight | genre energy | overview | franchise | director | cast | same-title | distinct genres | thin | cov |
|---|---|---|---|---|---|---|---|---|---|
| 0.00 | 0.00% | 26.52% | .813 | **.986** | .178 | .143 | **10.50** | .756 | .999 |
| 0.10 | 0.39% | 26.41% | .815 | **.986** | .178 | .150 | 10.44 | .589 | 1.000 |
| 0.20 | 1.19% | 26.19% | .819 | **.986** | .176 | .157 | 10.26 | .581 | 1.000 |
| 0.30 | 2.49% | 25.84% | .833 | **.986** | .170 | .143 | 9.94 | .570 | 1.000 |
| **0.44** | **5.07%** | 25.14% | .860 | .985 | .157 | .171 | 9.32 | .554 | 1.000 |
| 0.60 | 8.92% | 24.09% | .878 | .982 | .139 | .236 | 8.26 | .528 | .999 |
| 0.80 | 14.71% | 22.53% | .895 | .955 | .122 | .229 | 6.69 | .503 | .997 |
| 1.00 | 21.11% | 20.80% | **.903** | .754 | .106 | **.271** | 5.22 | .493 | .993 |

Phase 3 *predicted* that weight 0.44 yields ~5% genre energy, derived from the
square-root odds relation `w = sqrt(s/(1-s) · (1-s₀)/s₀)` with the measured
`s₀ = 21.11%`. The sweep measured **5.07%**. The prediction held, and the
weight was fixed before these results existed — it is not a fitted value.

The trade-off is monotone in both directions and there is no interior optimum on
any single metric:

* franchise recall rises .813 → .903 as genre strengthens;
* distinct genres per list collapses 10.50 → 5.22;
* **director recall collapses .986 → .754** — equal weighting actively destroys
  the person-based signal.

That last row is the decisive one, and it is the only row in this report driven
by a label that is conceptually independent of genre.

### 5.3 Cast bound sweep — hypothesis refuted

`cast.top_k` is applied during **tokenisation**, not vectorisation, so this sweep
rebuilds the feature corpus from raw. (The first run did not, and produced four
byte-identical experiments; `test_cast_top_k_experiments_rebuild_features` now
pins this.)

| top_k | dims | nnz | cast energy | cast recall | director recall | franchise | thin |
|---|---|---|---|---|---|---|---|
| 3 | 21,535 | 208,778 | 20.89% | **.115** | .739 | .904 | .506 |
| **5** | 23,404 | 226,352 | 21.06% | .106 | .754 | .903 | .493 |
| 10 | 27,643 | 269,205 | 21.16% | .083 | .759 | .907 | .444 |
| 20 | 33,762 | 341,644 | 21.18% | .075 | **.766** | .906 | **.380** |

The stated hypothesis was that thin-evidence matches would *rise* with `k`.
**It is the opposite**: thin falls monotonically .506 → .380, because more cast
terms per film means genuine matches share more terms. Franchise recall is flat
(±0.004) across the whole range.

Cast recall *falls* with `k`, .115 → .075, and that number is **confounded and
must not be read as "more cast is worse."** The cast *label* is built at
`top_k = 5` so it matches what the default vector saw. Index 10 actors and the
vector starts matching on credits 6–10, which the label does not count as
relevant. The comparison is only valid at `k = 5`.

`top_k = 5` is retained: it is Phase 3's documented choice, it is the value at
which the cast label is valid, and the sweep shows no franchise/director
justification for churning it.

### 5.4 Leave-one-field-out ablation (Δ recall@10 vs D)

| dropped | franchise | director | cast | same-title |
|---|---|---|---|---|
| overview | −0.049 | +0.006 | −0.002 | −0.100 |
| genres | −0.090 | **+0.232** | +0.072 | −0.128 |
| keywords | −0.106 | +0.042 | +0.006 | −0.071 |
| cast | −0.071 | −0.053 | −0.054 | −0.007 |
| director | −0.014 | **−0.657** | −0.004 | +0.061 |

Every field earns its place: no ablation leaves all four labels flat. Director is
the single most load-bearing field for its own label (−0.657) and genre is the
only field whose removal *helps* the director label (+0.232) — the same effect
seen in §5.2, now isolated.

Overview is the weakest contributor to structural labels (−0.049 franchise) but
carries the largest token share. It is retained because it is the only field
present on essentially every film, which is what makes coverage and breadth
possible (10.45 distinct genres/list for A).

### 5.5 Single field alone (recall@10)

| field alone | franchise | director | cast | same-title | distinct genres | thin |
|---|---|---|---|---|---|---|
| overview | .539 | .036 | .022 | .357 | 10.45 | .749 |
| genres | .266 | .035 | .017 | .079 | 2.92 | .912 |
| keywords | .661 | .061 | .031 | .200 | 9.31 | .944 |
| cast | .627 | .125 | **.602** | .000 | 10.79 | .951 |
| director | .366 | **.987** | .041 | .007 | 9.96 | .998 |

Cast alone reaches .602 on its own label and director alone reaches .987 — both
confirm the labels are measuring what they claim. In D those fall to .157 and
.985 respectively: cast is diluted to near-irrelevance, director is preserved.
Genre alone is the weakest signal (.266) even though genres hold 21% of D's
vector energy, which is precisely why genre is a tie-breaker rather than a
driver.

### 5.6 Evidence threshold sweep

| `min_shared_terms` | thin | coverage | candidates surviving (of 20) | distinct genres | popularity ρ |
|---|---|---|---|---|---|
| 1 (off) | .493 | .993 | 20.00 | 5.22 | .016 |
| 2 | .431 | .987 | 17.10 | 5.29 | .080 |
| **3** | **.284** | **.973** | **13.65** | 5.15 | .099 |
| 4 | .000 | .940 | **9.93** | 4.67 | .118 |
| 5 | .000 | .873 | 6.45 | 3.95 | .132 |
| 8 | .000 | .405 | 1.13 | 1.54 | .092 |

**Label recall is identical at every threshold.** This was initially a
correctness alarm, and it was a real bug: the filter ran *after* truncation to
20, so it could only delete and never promote a passing candidate from below the
cutoff. Fixed by fetching a pool 8× deeper and truncating after filtering
(`EVIDENCE_POOL_FACTOR`). The invariance is then a genuine property — 984 of 990
franchise siblings share ≥3 terms and sit at median rank 2, so nothing relevant
is ever at risk of being filtered.

`min_shared_terms = 4` is **measured and rejected**: it drives thin evidence to
0.000 but leaves a mean of 9.93 surviving candidates, so a 10-item request
cannot be filled. It is reported rather than hidden, because it is the reason 3
was kept despite a non-zero residual thin rate.

The filter has a real, non-obvious cost: **popularity bias rises with the
threshold** — ρ .016 (no filter) → .080 → .099 → .118 → .132 at 5, then back to
.092 at 8 where only 1.13 candidates survive to be ranked at all — because
films with many credits accumulate shared terms and skew more popular.
`thin_evidence_rate` cannot see this.

### 5.7 Popularity diagnostics

| | Spearman ρ | mean popularity percentile |
|---|---|---|
| rep_A | .008 | 50.1 |
| rep_D | .016 | 44.7 |
| **selected** | **.051** | **52.4** |

Near-zero rank correlation and a mean percentile near the neutral 50. The system
is **not** steering to blockbusters. This is reported, not optimised.

---

## 6. Qualitative check — eight categories

`python scripts/qualitative_check.py`, run against the **selected**
configuration (fingerprint `af683a71…`). Selection rules are deterministic and
documented in the script. Full output in
`data/processed/qualitative_check.json`.

> **These outcomes predate the §9 backfill fix and are not restated.** The
> script calls `Recommender.recommend()` at `min_shared_terms = 3`, so a
> "0 recommendations" or "10 candidates rejected" figure below is a *10-deep*
> window, not a 160-deep one. The fix changes what several of these categories
> would now return; the recorded values are kept as the historical record, and
> `qualitative_check.json` was deliberately not regenerated. Read the short lists
> below as a lower bound on what production now produces.

| # | Category | Query | Outcome (pre-fix) |
|---|---|---|---|
| 1 | popular | *Resident Evil: Welcome to Raccoon City* (highest popularity) | 4 of 8 are *Resident Evil* films or *Dawn of the Dead*. Strong. |
| 2 | obscure | *Each for the Other* (lowest TMDb popularity) | **0 recommendations.** 10 candidates rejected below threshold. |
| 3 | sparse features | *სოლო სალამურისათვის* (1 non-zero term) | **0 recommendations.** Correct: nothing to match on. |
| 4 | no overview | *Sokak Kızı* | **1 recommendation** (*Yavrum*, 0.48, shared=4). Other fields carry it. |
| 5 | documentary | *The Bloody Hundredth* | 2 results, genre-plausible (*Liblikapüüdja* 0.24, *Battle of Britain* 0.14). Thin but not wrong. |
| 6 | animation / family | *Curly Sue* | 5 of 9 carry comedy. Two 3-term false positives (a Bond *documentary*, a 1986 drama). |
| 7 | same title | *Lord of the Flies* (1963) | **Fails.** 1990 remake absent; list is unrelated films. |
| 8 | franchise | *Freddy vs. Jason* (6-film collection) | 4 of 5 siblings in the top 8. Strong. |

Three honest failures are recorded rather than hidden:

* **Categories 2 and 3 return nothing.** For films with 1–17 tokens the system
  has no evidence, and the correct behaviour is an empty list, not ten weak
  guesses. The product implication is in §8.
* **Category 7 fails, and genre weight is why.** At `weight = 1.0` the 1990
  *Lord of the Flies* ranks **1st** for the 1963 original at cosine 0.339 with 15
  shared terms; at `0.8` it drops to rank 2; at the shipped `0.44` it leaves the
  top 10 entirely, and same-title recall falls .271 → .171. Down-weighting genre
  measurably costs this label. It is the clearest single cost of the §7 decision.
* **Category 6 shows the residual `shared == 3` noise.** The metric alone would
  have declared `min_shared_terms = 3` clean; looking at the output is what
  revealed the boundary is where the visible errors are. That is why 4 was
  measured — and then rejected for the list-length reason in §5.6.

---

## 7. The genre decision, argued rather than read off a number

The brief mandates a genre-weight sweep. §5.2 delivers it. It does **not**
deliver a validated optimum, and pretending otherwise would be the main
dishonesty available in this phase.

**What cannot be done.** No label in this dataset is independent of genre
(§3.5). Franchise recall rising with genre weight is not evidence that genre
weight is right; it is partly genre restating itself. Choosing `1.0` because it
maximises franchise recall would be circular reasoning dressed as tuning.

**The tie-break actually used.** The director label is the only proxy that is
*conceptually* independent of genre — a director's films span genres by
definition. It is therefore the only row of the sweep entitled to break a tie
that genre-correlated labels cannot.

* `weight ≤ 0.3` → director recall **.986**
* `weight = 0.44` → director recall **.985** (indistinguishable)
* `weight = 1.0` → director recall **.754** (−0.232, a collapse)

Genre at equal weight does not merely fail to help — it actively drowns the one
signal that can disagree with it.

**Secondary consideration.** Genre is a high-precision, low-resolution signal:
19 types, 5% energy. Used as a modest tie-breaker it *reduces* junk
(thin .756 → .554) while preserving breadth (10.50 → 9.32 distinct genres).
Pushed to 21% it buys .043 franchise recall for a 45% collapse in list breadth.

**Decision: `genres.weight = 0.44`.** Justified because it was derived in Phase 3
before these results existed, it sits at the knee rather than any argmax, it
retains **99.9%** of maximum director recall (.985/.986) and **89%** of maximum
genre breadth (9.32/10.50), and it is the point where genre helps without
becoming the answer. It maximises no metric, which is the point.

**Stated plainly:** this is a reasoned default under an unvalidatable
constraint, not a measured optimum. A user who prefers narrow, same-genre lists
should raise it toward 0.8; the sweep is in the artifact for exactly that.

---

## 8. Selected configuration and rationale

```
representation      D  (overview + genres + keywords + cast + director)
block_mode          per_block
genres.weight       0.44          -> 5.07% of vector energy
min_shared_terms    3             -> rejects <3 shared non-zero terms
cast.top_k          5             (Phase 3; label-aligned)
k                   10
similarity          cosine (dot product over L2-normalised TF-IDF)
fingerprint         af683a71bb38bf690c6e6fe45a20c49f112dc4b97dc7539f1b416f40f9640fd7
```

Per-K, selected configuration:

| Label | n | recall@5 | recall@10 | recall@20 | hit@10 | NDCG@10 | mean relevant |
|---|---|---|---|---|---|---|---|
| franchise | 528 | .769 | .860 | .914 | .909 | .774 | 1.88 |
| director | 3,125 | .928 | .985 | .991 | .997 | .978 | 3.27 |
| cast | 4,498 | .104 | .157 | .247 | .605 | .413 | 19.15 |
| same_title | 140 | .100 | .171 | .350 | .171 | .077 | 1.04 |

Label-free: coverage .986, intra-list diversity .914, distinct genres 8.28,
distinct decades 4.40, mean top-1 cosine .280, thin evidence .332, popularity
ρ .051, mean popularity percentile 52.4, mean surviving candidates 12.45 of 20.

**Product consequence, from the data.** The mean query yields 12.45 candidates
at the evidence threshold, so ten is normally fillable — but not always, and
categories 2–3 of the qualitative check yield **zero**. The UI must therefore be
willing to return *fewer than ten* recommendations and say so, rather than pad
with 3-term matches. That is a deliberate choice: `min_shared_terms = 4` would
guarantee ten items and would also guarantee that roughly a third of them rest on
three shared words.

**A short list is now an honest signal.** As of the §9 fix, a result shorter
than `k` means the catalogue genuinely held too few candidates at the evidence
threshold — not that the engine failed to look further down the ranking. The two
cases are indistinguishable to a user, so the UI must still surface the count.

**The 12.45 and the qualitative figures were measured before the §9 fix, and
that is now the only reason they need a caveat.** The offline evaluator has
always scored an 8× deeper pool so the filter could promote passing candidates;
the production `Recommender.recommend()` did not, so it could only delete from a
`k`-deep window. Both numbers in §5 and §6 are therefore a slight *upper* bound
on what a live `k = 10` call used to return. `recommend()` now backfills, so the
gap is closed in production and §9 reports the measured before/after. The
recorded §5 and §6 results are left exactly as measured.

---

## 9. Production correctness patch (post-measurement)

Everything in §5–§8 above was measured against an engine that could not backfill.
Limitation 11 recorded that as a known defect. It is fixed here. The reported
Phase 4 results are **not** restated; this section records what changed in
production and what it cost.

### 9.1 The defect

`recommend()` retrieved the top `k` rows and *then* applied `min_shared_terms`.
A candidate rejected at rank 11 was never replaced by a passing one at rank 12.
The filter could only ever delete, never promote — so a short list carried two
inseparable meanings: "the catalogue had nothing better" and "the engine stopped
looking". The offline evaluator was never affected; it already scored an 8× pool
(`EVIDENCE_POOL_FACTOR = 8`).

At the selected configuration this was not a rare edge case. Over all 5,000
queryable films, `k = 10`, `min_shared_terms = 3`:

| | Pre-fix | Post-fix |
|---|---|---|
| Mean results returned | 6.49 | 9.83 |
| Queries returning fewer than `k` | 4,529 (90.6%) | 111 (2.2%) |
| Queries returning exactly 10 | 471 | 4,889 |

4,460 queries gained at least one slot; 16,664 recommendation slots were
recovered. The residual 111 short queries are genuine exhaustion — after the fix
a short list means the catalogue really did hold too few candidates at the
threshold.

### 9.2 The change

`recommend()` now ranks the full candidate set once and walks it in order,
filtering as it goes and stopping at `k` accepts. `sparse_top_k()` is unchanged
as a public contract and still carries its `argpartition` fast path; it now
shares a scoring helper with the new `ranked_candidates()`. `evaluate.py` was not
touched.

Two details matter for the contract rather than the speed:

* The scan consumes **one** total order, so a promoted candidate cannot appear
  out of rank order. `tests/test_recommend.py` pins this against an unfiltered
  ranking.
* `skipped` reports only the candidates actually examined, so a query that fills
  from rank 4 does not claim to have rejected the whole catalogue.

### 9.3 Cost

| | Pre-fix | Post-fix |
|---|---|---|
| `recommend()`, `k = 10`, `mt = 3` | 1.5 ms | 2.4 ms |

A scan that visits hundreds of rows instead of ten cannot be free. Two changes
kept it to 1.5× rather than the 2.7× a naive version cost: reading candidate rows
straight from the CSR `indptr`/`indices` arrays instead of slicing
`self._matrix[idx]` (which allocated a fresh 1×D sparse object per candidate),
and counting shared terms through a feature-space boolean mask rather than
`np.intersect1d`, which re-sorted both operands on every call. The full sort is
retained rather than a bounded `argpartition` window, because a window can drop
a tied candidate that the filter would have accepted.

### 9.4 Regression net

`tests/test_recommend.py` grew 37 → 53 tests. The core fixture is a hand-built
7×4 matrix where the top-3 by score are exactly the three candidates the filter
must reject, and the valid candidates sit at ranks 4–6 — so the pre-fix code
returns nothing where a correct implementation returns three. 6 of the new tests
fail against the pre-fix engine and pass after it. One test exists purely to
assert the fixture still has that property, so the regression net cannot rot into
a tautology.

### 9.5 Artifacts

No artifact was regenerated; all four hashes in §10 are unchanged. This is
verified rather than assumed: every sample stored in `experiments.json` is
`min_shared_terms = 1`, where the gate is provably inert (a strictly positive
cosine score on non-negative TF-IDF rows implies at least one shared term), so
backfill cannot trigger — confirmed empirically, 0 rejections across 715 queries.
`qualitative_check.json` is the one artifact the fix *would* change, since it
runs at `min_shared_terms = 3`. It is deliberately left as-is: its figures are
the record of the pre-fix behaviour this section corrects.

---

## 10. Reproducibility

```bash
python scripts/build_features.py            # Phase 3 corpus
python scripts/build_evaluation_labels.py   # proxy labels from raw
python scripts/run_experiments.py           # 33 experiments
python scripts/qualitative_check.py         # eight categories
python -m pytest                            # 222 tests
```

| Artifact | SHA-256 |
|---|---|
| `data/raw/tmdb_movies.jsonl` | `5b1079cd46efad0dec21f74736bebc6db6ba2f91e1b7497365c86225fd36307b` |
| `data/processed/movies.jsonl` | `53b0b8c675651ea438230fe75bba3660f52213428876f9cb99e565825fcdece0` |
| `data/processed/evaluation_labels.json` | `7717c4283a58f58b…` |
| `data/processed/experiments.json` | `ee80af1c529d6359…` |
| `data/processed/experiment_manifest.json` | `3b2a1ddf4fc5f35c…` |

The raw hash is unchanged from Phase 2/3. The selected config fingerprint is
`af683a71…`; it differs from the Phase 3 corpus fingerprint (`4615a843…`) for
exactly one reason — `genres.weight` is `0.44` here versus `1.0` there. Both use
`block_mode='per_block'`, and `data/processed/movies.jsonl` is byte-identical to
the Phase 3 output, because the genre weight is a vectoriser parameter, not a
corpus change.

**Both result artifacts are byte-reproducible.** Two consecutive full runs
produce identical SHA-256. This required removing wall-clock `seconds` from the
results payload: it was the only non-deterministic quantity, and its presence
meant no two runs could ever hash equal. Runtime is printed to the console
instead.

---

## 11. Files changed

| File | Purpose |
|---|---|
| `moviemind/labels.py` | Structural proxy labels; raw-only, with the processed-corpus guard |
| `moviemind/recommend.py` | Sparse deterministic top-K engine |
| `moviemind/evaluate.py` | Metrics, metric definitions, leave-one-out protocol |
| `moviemind/experiments.py` | 33-experiment matrix, runner, reproducibility manifest |
| `scripts/build_evaluation_labels.py` | Builds the label artifact from raw |
| `scripts/run_experiments.py` | Runs the matrix, writes results + manifest |
| `scripts/qualitative_check.py` | Eight-category qualitative check |
| `tests/test_labels.py` | 23 tests |
| `tests/test_recommend.py` | 53 tests (37 at Phase 4 close, 16 added for the §9 backfill fix) |
| `tests/test_experiments.py` | 22 tests |

`moviemind/labels.py` was extended from its Phase 3 stub into the full
`ProxyLabels` implementation. Apart from that, no Phase 1–3 module was modified:
`config.py`, `pipeline.py`, `representations.py` and `text.py` are unchanged, and
the Phase 3 corpus is byte-identical (hash above).

The §9 patch then modified `moviemind/recommend.py` and `tests/test_recommend.py`
only. `evaluate.py`, `experiments.py`, `labels.py` and the selected configuration
are untouched, which is why the §5 results stand unchanged.

---

## 12. Limitations

1. **No relevance ground truth exists.** Every number in §5 is agreement with a
   structural proxy. None of it is user satisfaction, and none of it can be.
2. **The genre decision is argued, not validated** (§7). The strongest
   independent signal available is a proxy that is conceptually but not
   empirically independent of genre.
3. **Same-title is a noisy label** and genre weight measurably degrades it
   (.271 → .171, §6 category 7).
4. **Cast is diluted in D.** It reaches .602 on its own label but .157 inside the
   full representation. A user searching by actor is poorly served.
5. **A third of the selected configuration's output rests on ≤3 shared terms**
   (§5.6). The `shared == 3` boundary is where the visible false positives
   live, and no measured threshold removes it without dropping below ten results.
6. **Shared-term count is a crude evidence measure.** It ignores term
   informativeness and query length: a 60-token film shares 3 terms trivially, a
   5-token film cannot. A share-of-query or IDF-weighted floor would be better
   and was out of scope.
7. **The evidence filter introduces popularity bias.** Holding genre weight at
   0.44, popularity ρ rises .007 → .051 (§5.6 shows the same effect at 1.0,
   .016 → .132). Popular films accumulate shared terms and survive the filter
   more often, so the filter quietly improves proxy scores while worsening
   novelty — and no proxy label can see it.
8. **Sparse films get no recommendations** (§6, categories 2–3). This is correct
   behaviour, but it means coverage .986 is an average that hides a hard floor.
9. **The snapshot is popularity-skewed and 6 months old.** TMDb requires
   re-validation within 6 months; these results do not transfer to a refreshed
   snapshot without re-running.
10. **Licensing.** TMDb data is restricted to non-commercial retrieval and
    similarity. Nothing here is a trained model, and nothing may be
    redistributed. Attribution is mandatory.
11. **The online engine could not backfill past the evidence filter.**
    **Resolved in §9; retained here because the §5–§8 results were measured
    before the fix.** `recommend()` used to retrieve the top `k` and only then
    apply `min_shared_terms`, so a candidate rejected at rank 11 was never
    replaced by one passing at rank 12. Reported list lengths were therefore
    measured on a deeper pool than production used, and the "12.45 candidates"
    figure was optimistic for a live `k = 10` call. At the selected
    configuration 90.6% of queries returned fewer than ten results. Fixed by
    ranking the full candidate set and filtering during the scan.
12. **Backfill finds more evidence; it does not create it.** The fix cannot
    manufacture a valid recommendation where none exists — the residual 2.2% of
    queries still short of `k` are genuine exhaustion, and a lower
    `min_shared_terms` is still the only lever that would trade evidence quality
    for coverage (§8). It also costs 1.5× query latency, which is immaterial at
    5,000 films but would need revisiting against a much larger index.

---

## 13. Phase 4 checklist

| Requirement | Status |
|---|---|
| Build a recommendation engine | Done — sparse cosine top-K, `moviemind/recommend.py` |
| Evaluate against at least one meaningful signal | Done — four structural proxies, reported separately |
| Run a genre-weight experiment | Done — 8 weights, §5.2, prediction confirmed at 5.07% |
| Run a field-contribution experiment | Done — 5 ablations + 5 single-field, §5.4–5.5 |
| Include an overview-only baseline | Done — representation A, §5.1 |
| Address popularity bias | Done — two diagnostics, reported and not optimised |
| Qualitative sanity check, 8 categories | Done — §6, including three recorded failures |
| Select a configuration with justification | Done — §7–8, argued under a stated constraint |
| Record all experiments reproducibly | Done — byte-identical artifacts, full manifest |
| No backend / frontend / auth / DB | Confirmed — none added |
| No supervised ML on TMDb content | Confirmed — nothing is fitted; weights set by hand |
| Stop after this phase | Confirmed |
