# Phase 2 — Dataset Audit: TMDb Movie Metadata Snapshot

**Status:** COMPLETE
**Date:** 2026-09-26
**Decision:** **A. DATASET APPROVED** (non-commercial, local-only, with the mandatory conditions in §15)
**Scope of this phase:** acquire, verify, and audit the dataset only. No preprocessing, model, or application code was written.

---

## 1. Dataset identity

| Property | Value |
| --- | --- |
| Working name | `tmdb_movies.jsonl` (MovieMind snapshot v1) |
| Local path | `data/raw/tmdb_movies.jsonl` |
| Format | JSON Lines, one UTF-8 JSON object per line |
| Records | 5,000 films |
| Size on disk | 13,964,467 bytes (13.32 MiB) |
| SHA-256 | `5b1079cd46efad0dec21f74736bebc6db6ba2f91e1b7497365c86225fd36307b` |
| Provenance manifest | `data/raw/tmdb_manifest.json` |
| Audit outputs | `data/audit/audit_summary.json`, `data/audit/audit_report.md` |
| Release window | 1960-01-01 → 2025-12-31 |
| Owner of the data | The Movie Database (TMDb), operated by TiVo Platform Technologies LLC |
| Licence | Non-commercial, attribution-required, no redistribution |

This is a **derived, purpose-built snapshot**, not a named public release. TMDb publishes daily ID exports, but they carry no metadata, so the fields MovieMind needs had to be assembled through the official API. That derivation is fully described by `scripts/fetch_tmdb_snapshot.py` and pinned by the SHA-256 above.

## 2. Exact source

- **API root:** `https://api.themoviedb.org/3/`
- **Discovery endpoint:** `GET /discover/movie` (primary release year filter, `sort_by=popularity.desc`)
- **Detail endpoint:** `GET /movie/{movie_id}` with `language=en-US` and `append_to_response=credits,keywords`
- **Authentication:** TMDb v4 read access token, sent as an `Authorization: Bearer` header
- **Credential storage:** `data/../.env` (i.e. `D:\MovieMind\.env`), git-ignored. The token is never written to the manifest, the dataset, or any log line.

Rejected alternatives, carried forward from Phase 1:

| Source | Why not |
| --- | --- |
| MovieLens `ml-latest-small` | 9,742 movies but tags on only 16.14%; no overview, cast, director, or keywords. Content features are not derivable. |
| IMDb non-commercial datasets | Licence bars storing/deriving an offline movie database; a recommender needs exactly that. |
| Wikidata | CC0 and safe, but carries no plot overview and no keyword set. |
| Kaggle / GitHub / Google Drive mirrors | No verifiable provenance, unknown licence, unknown staleness. |

TMDb's own bulk ID exports at `files.tmdb.org` returned **HTTP 403** for every date tested, and would in any case supply IDs only.

## 3. Provenance

The chain is fully traceable end to end:

1. **Origin** — TMDb, an editorially curated database. Values such as overviews, keywords, and credits are human-entered and therefore imperfect; they are not a neutral ground truth.
2. **Retrieval** — official API only, with a credential registered by the project owner. No scraping, no third-party mirror.
3. **Sampling** — deterministic. Seed `20260926`, stratified across 66 release years, 76 films per year, random selection from a 200-title per-year discovery pool.
4. **Projection** — a documented, lossy field selection applied at acquisition time (§5).
5. **Integrity** — SHA-256 of the finished file recorded in both the manifest and `audit_summary.json`; the two agree.
6. **Storage separation** — raw data lives outside version control, confirmed by an actual `git init` + `git add -A` test (§14).

## 4. Licensing and terms analysis

**Terms consulted (primary text, fetched and read in full):**

- API Terms of Use — `https://www.themoviedb.org/documentation/api/terms-of-use` (last updated 2023-10-20)
- Main Terms of Use — `https://www.themoviedb.org/terms-of-use`

| Question | Answer | Basis |
| --- | --- | --- |
| Free non-commercial API use? | **Yes** | API ToU §1.A; main ToU §3.A "personal, non-commercial use" |
| Commercial use? | **No** — needs a separate written agreement | API ToU §1.A, at TMDb's sole discretion |
| Local storage of a snapshot? | **Yes**, with conditions | API ToU §1.C; staff guidance ties it to periodic refresh |
| ML/AI clause? | **Permitted for retrieval, prohibited for training** | API ToU §1.C plus staff clarification, below |
| Derived features / vectors? | **Yes**, non-commercial | Staff guidance, below |
| Public GitHub repository? | **Code yes, data no** | No redistribution right; verified by ignore test |
| Public deployed demo? | Non-monetised portfolio deployment **acceptable**, at TMDb's discretion | Staff guidance, below |
| Attribution? | **Mandatory** | API ToU para. 3 |
| Cache limit? | **6 months** | API ToU §1.C |

**Required attribution notice (verbatim, must appear in the app):**

> This [website, program, service, application, product] uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.

**The ML/AI clause — resolved, not assumed.** On its face API ToU §1.C appears to forbid using TMDb content "in connection with, or for training, a machine learning or artificial intelligence based Application," which would appear to capture a TF-IDF recommender. TMDb staff have since stated the clause's purpose directly:

- Student building a **TF-IDF model** over TMDb data, non-commercial → *"Yup, there is no issue with this."* — Travis Bell (staff), 2026-09-18
- Non-commercial app storing **derived vectors** → *"storing embeddings like this is fine… remember to attribute TMDB as the source of the data."* — Travis Bell (staff), 2026-09-17
- On the clause's intent → *"the purpose of the language in the terms of use is primarily around using TMDB for **training** purposes. This is prohibited. There is no issue if you are simply using an LLM."* — Travis Bell (staff), 2026-09-19
- Developer building a *"strictly non-commercial movie recommendation system for my personal portfolio"*, storing metadata plus vectors → confirmed acceptable, on the condition that content be **refreshed periodically**, which matches the 6-month cap.

**Honest caveat.** These are informal staff statements in a public forum, not a contract amendment. The ToU reserves the right to change terms and to terminate the licence at TMDb's sole discretion. They remove the *apparent* prohibition on MovieMind's architecture; they do not constitute legal advice or a guarantee. If MovieMind ever becomes monetised, or is trained on, this section must be revisited **before** that happens.

## 5. Acquisition method

`scripts/fetch_tmdb_snapshot.py`, official API only.

**Sampling.** TMDb's `/discover/movie` is ordered by popularity, so a naive top-N pull yields only blockbusters. Instead, for each of the 66 years from 1960 to 2025 the script:

1. builds a pool of up to 200 candidate IDs from that year's discovery pages,
2. randomly samples 76 without replacement using seed `20260926`,
3. tops up from any remaining pool if a year is short.

Achieved spread: **74–76 films per year**, 66/66 years populated, no year under target.

**Residual bias, stated plainly.** Randomising within a popularity-ordered pool dilutes but does not remove the popularity bias. This snapshot over-represents *better-known* films relative to the full TMDb catalogue. It is a defensible choice for a demo catalogue, and it is not a uniform sample of TMDb.

**Projection (lossy, deliberate).** Retained: `id`, `title`, `original_title`, `overview`, `tagline`, `genres`, `keywords`, `cast` (top 20 by billing order, with character and order), `crew` (Director/Screenplay/Story only), `release_date`, `runtime`, `original_language`, `status`, `popularity`, `vote_average`, `vote_count`, `adult`, `belongs_to_collection` (id + name).

Dropped: `budget`, `revenue`, `homepage`, `production_companies`, `production_countries`, `spoken_languages`, `origin_country`, `imdb_id`, `video`, and all image paths. Every dropped field is recoverable by re-running the script; the projection is the only thing that would need widening.

**Operational properties.** Resumable via `--resume` (verified: an interrupted run resumed from 4,191 records and finished at exactly 5,000 with no duplicates or gaps). Rate-limited with backoff on HTTP 429. `include_adult=false`, so the snapshot contains **0 adult-flagged records**. 0 fetch errors.

## 6. Dataset version and date

- **Acquired:** 2026-09-26, 07:16:35Z → 07:28:57Z (official API timestamps)
- **Audited:** 2026-09-26, 07:30:30Z
- **Snapshot version:** v1, identified by SHA-256 `5b1079cd…6307b`
- **Data as-of:** TMDb is a live database, so this is a point-in-time capture. Popularity, vote totals, and keyword curation will drift.

The manifest records the run in two parts because the first run was interrupted by the operator: 4,191 records in the initial pass, 809 in the resumed pass, same seed and same plan, together forming the 5,000-record file. This is disclosed rather than hidden, and the SHA-256 pins the result regardless.

## 7. Storage requirements

| Item | Size |
| --- | --- |
| `data/raw/tmdb_movies.jsonl` | 13.32 MiB |
| `data/raw/tmdb_manifest.json` | ~11 KiB |
| `data/audit/audit_summary.json` | ~20 KiB |
| `data/audit/audit_report.md` | ~6 KiB |
| **Total** | **≈ 13.4 MiB** |

Comfortably inside a typical laptop budget, including derived TF-IDF matrices in Phase 3. Mean record size is ~2.8 KB. No images, video, or audio are stored, which is what keeps it this small.

## 8. Complete audit statistics

Produced by `scripts/audit_dataset.py`, which is **strictly read-only** — it modifies, drops, and deduplicates nothing.

### Integrity

| Metric | Value |
| --- | --- |
| Lines read | 5,000 |
| Records parsed | 5,000 (100.00%) |
| Malformed lines | 0 |
| Unique IDs | 5,000 |
| Duplicate ID rows | 0 |
| Invalid ID rows | 0 |

### Field completeness

| Field | Present | Missing |
| --- | --- | --- |
| `id` | 5,000 (100.00%) | 0 |
| `title` | 5,000 (100.00%) | 0 |
| `release_date` | 5,000 (100.00%) | 0 |
| `overview` (prose ≥ 40 chars) | 4,913 (98.26%) | 87 (1.74%) |
| `genres` | 4,944 (98.88%) | 56 (1.12%) |
| `keywords` | 4,524 (90.48%) | 476 (9.52%) |
| `cast` | 4,956 (99.12%) | 44 (0.88%) |
| `director` | 4,992 (99.84%) | 8 (0.16%) |

### Usability tiers

| Tier | Requirement | Count | % |
| --- | --- | --- | --- |
| **Full** | all eight fields present and usable | 4,493 | 89.86% |
| **Usable (core)** | id, title, release_date, genres, cast | 4,910 | 98.20% |
| **Minimal** | id, title | 5,000 | 100.00% |

### Overview text length (n = 4,931; 18 stubs and 69 blanks excluded)

| Statistic | Characters | Words |
| --- | --- | --- |
| Min | 12 | 2 |
| p25 | 160 | 27 |
| Median | 243 | 42 |
| Mean | 271.18 | 46.16 |
| p75 | 362 | 62 |
| p95 | 512 | 88 |
| Max | 1,000 | 169 |

Median 42 words per overview is comfortably sufficient for TF-IDF; p25 of 27 words sets the realistic floor.

### Field structure

| Field | Min | p25 | Median | Mean | p75 | p95 | Max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Genres per film | 1 | 2 | 2 | 2.49 | 3 | 4 | 7 |
| Keywords per film | 1 | 5 | 9 | 10.46 | 15 | 24 | 98 |
| Cast per film | 1 | 18 | 20 | 17.92 | 20 | 20 | 20 |
| Runtime (min) | 2 | 92 | 102 | 103.17 | 114 | 141 | 360 |

Cast order is present, ascending, and already billing-sorted in 4,956/4,956 records — credit order is a usable signal, not just an arbitrary list.

### Duplicates and junk

| Metric | Value |
| --- | --- |
| Duplicate exact-title groups | 69 (140 rows) |
| Duplicate normalised-title groups | 71 |
| **Redundant rows (same normalised title *and* year)** | **0** |
| Junk-title rows | 1 (`xXx`, id 7451) |
| Numeric-only titles | 6 (`2012`, `1776`, `54`, `9`, `42`, `1408`) |

The headline finding: **there are zero true duplicates.** All 69 title collisions are remakes and re-releases of genuinely different films (`The Enforcer` ×3, `Revenge` ×3, `Mulan` ×2, `Havoc` ×2). Zero collision groups share a release year. **Do not deduplicate by title** — that would delete real films.

The 6 numeric titles are false positives of the junk detector, not junk. `2012`, `1776`, `54`, `9`, `42`, and `1408` are all correct.

### Distributions

- **Genres:** 19 distinct. Drama 2,161; Comedy 1,695; Thriller 1,052; Action 1,041; Adventure 851; Romance 806; Crime 772; Horror 616; Family 551; Science Fiction 514; Fantasy 491; Animation 405; Mystery 367; History 241; War 188; TV Movie 185; Western 155; Music 140; Documentary 96.
- **Keywords:** 11,914 distinct, of which **6,342 (53.2%) are singletons**. Reusable vocabulary: 5,572 appear ≥2×, 2,005 ≥5×, 922 ≥10×, 390 ≥20×.
- **Most common keywords:** `based on novel or book` 572, `sequel` 384, `murder` 298, `based on true story` 214, `new york city` 200, `duringcreditsstinger` 192, `woman director` 182, `revenge` 172, `aftercreditsstinger` 149, `biography` 128.
- **Languages:** `en` 3,701 (74.0%); non-English 1,299 (26.0%) — fr 256, it 231, ja 187, es 122, de 76, cn 60, zh 42, ko 41.
- **Collections:** 1,127 films (22.5%) belong to a named franchise — James Bond 10, Dragon Ball Z 8, Friday the 13th 6, Halloween 6, Police Academy 5.
- **Adult-flagged:** 0 (by request).
- **Votes:** median `vote_count` 396; 505 films have <10 votes, 1,085 have <50, and 126 have zero votes with `vote_average` 0.0 (i.e. unrated, not "0 stars").

## 9. Data quality findings

**Two projection bugs were found and fixed before this report was written** — both caught by a 12-record smoke test rather than by the full run:

1. **Keywords were silently empty (100% loss).** TMDb's `append_to_response=keywords` returns the block as `{"keywords": [...]}`, not `{"results": [...]}`. The projection read the wrong key, so all 5,000 films appeared to have zero keywords. Caught at 0/12 on a smoke test; the real fill rate is 90.48%.
2. **Collection names were discarded.** The projection stored a bare boolean instead of the collection name, throwing away the franchise name. Now stores `{id, name}`; 1,127 films recover their franchise.

**One audit bug was found and fixed:**

3. **Fabricated duplicate groups from non-Latin titles.** Title normalisation used an ASCII-only character class, so every CJK, Georgian, and Greek title collapsed to the empty string. This invented a bogus 7-film "duplicate group" (unrelated films: `松山之殇`, `სოლო სალამურისათვის`, `丫丫`, `ბერიკაცები`, `赤道迷情`, `Το αγρίμι`, `野火`) and one false "same title + same year" duplicate (two different Georgian films both dated `1975-01-01`). Normalisation is now Unicode-aware with a non-collapsing fallback. Corrected counts: 71 normalised groups, **0 redundant rows**.

All three bugs inflated apparent data quality problems. The corrected numbers are the ones in §8.

**Genuine quality issues that remain in the source data:**

- **Credits-stinger noise.** `duringcreditsstinger` (192), `aftercreditsstinger` (149), `beforecreditsstinger` (1) pollute **275 films (5.5%)**. These describe post-roll content, not the film, and are actively harmful as similarity features — they would make unrelated films look alike.
- **Singleton keyword explosion.** 53.2% of the vocabulary occurs exactly once. Left unfiltered, these become thousands of noise dimensions that match nothing and inflate the apparent vocabulary.
- **`runtime: 0` means unknown.** 80 films have `runtime` of 0 or null. TMDb uses 0 for "not recorded"; treating it as a 0-minute film is wrong. A further 111 films run 1–39 minutes — mostly shorts, TV films, and a few adult entries.
- **18 stub overviews** are real text but near-useless, typically `"Pakistani Film"` or `"Film starring Nadeem & Deeba"`.
- **126 unrated films** have `vote_average` 0.0, which is absence of data, not a low score.
- **Spotty keyword coverage on foreign-language films.** Overviews are fetched in `en-US`, so non-English titles often rely on keywords alone for descriptive text.
- **TMDb is crowd- and editor-curated.** Popularity and vote figures reflect who uses the site.

## 10. Missing-data analysis

| Field | Missing | Rate | Assessment |
| --- | --- | --- | --- |
| `keywords` | 476 | 9.52% | The real gap. Worst on obscure and non-English titles. Recoverable by widening the sampling pool or backfilling `/movie/{id}/keywords`. |
| `overview` (blank) | 69 | 1.38% | Minor. Genre, cast, and keywords still describe these films. |
| `overview` (stub) | 18 | 0.36% | Filterable by length. |
| `genres` | 56 | 1.12% | Minor; derivable from keywords. |
| `cast` | 44 | 0.88% | Minor. |
| `director` | 8 | 0.16% | Negligible. |

**Missingness is not random.** It concentrates in long-tail, non-English, and low-attention titles — exactly the films a popularity-ordered pool under-samples. A 9.52% keyword gap on the full set would be higher still on a popularity-neutral sample.

**Impact on the recommender.** Immaterial for a full-timeline view: 89.86% of films carry every field, and 98.20% carry the core five. The pipeline should degrade gracefully — a film with no keywords still has a 42-word median overview, genres, and cast.

**Viable full coverage:** 4,508 films (90.2%) have both keywords *and* a prose overview, which is the intersection that makes a keyword-plus-text hybrid vector work well.

## 11. Proposed cleaning rules

**Not applied.** The audit modified nothing. These are recommendations for Phase 3, to be implemented in a separate, versioned step.

| # | Rule | Rationale |
| --- | --- | --- |
| 1 | **Never deduplicate by title.** Key on TMDb `id`. | 0 true duplicates; 69 groups are legitimate remakes. Title-dedup would delete real films. |
| 2 | Treat `runtime == 0` and null as **missing**, not 0. | TMDb's sentinel for unrecorded. |
| 3 | Drop credits-stinger keywords (`duringcreditsstinger`, `aftercreditsstinger`, `beforecreditsstinger`). | Harmful noise; affects 275 films. |
| 4 | Drop `overview` shorter than 40 characters. | Removes 18 stubs. |
| 5 | Keyword `min_df` ≥ 2, ideally ≥ 5. | 53.2% of vocabulary is singletons. |
| 6 | Exclude the 8 director-less films from director-based features only, not from the catalogue. | Keep the film; drop the feature. |
| 7 | Keep films missing keywords, with keyword vectors zero-filled or omitted. | 476 films; still describable from other fields. |
| 8 | Exclude `vote_average == 0.0` from any rating-derived feature or sort. | 126 unrated films; absence ≠ zero. |
| 9 | Keep `xXx` (id 7451). | The single "junk title" is a real film. Do not apply a junk filter. |
| 10 | Preserve `original_title`; use `title` for display, both for matching. | 116 non-ASCII titles; `en-US` titles are transliterations. |
| 11 | Record `collection.name` as a categorical franchise feature. | 1,127 films carry one. |
| 12 | Never impute from an external source. | Would break provenance. |

**Expected yield after rules 1–8:** 5,000 films retained (nothing dropped), with clean vectors for ~4,900 and graceful degradation for the rest.

## 12. Candidate recommendation features

Ranked by expected value for a TF-IDF + cosine-similarity recommender.

| Rank | Feature | Source field | Coverage | Notes |
| --- | --- | --- | --- | --- |
| 1 | Overview text | `overview` | 98.26% | Primary signal. Median 42 words. Best TF-IDF input. |
| 2 | Keywords | `keywords[]` | 90.48% | Median 9/film. Needs `min_df` and stinger removal. |
| 3 | Genre | `genres[]` | 98.88% | Median 2/film. 19 classes. Coarse but reliable. |
| 4 | Cast | `cast[].name` | 99.12% | Median 20. Strong for actor-based similarity; needs name canonicalisation. |
| 5 | Director | `crew[job=Director]` | 99.84% | 285 films are co-directed — must be a *set*, not a scalar. |
| 6 | Screenplay / Story | `crew[job∈{Screenplay,Story}]` | 99.1% / 33.9% | Writer as an author signal. Story is sparser. |
| 7 | Franchise | `belongs_to_collection.name` | 22.5% | High-precision on 1,127 films. |
| 8 | Character | `cast[].character` | with cast | Free extra text for the vector. |
| 9 | Era / decade | `release_date` | 100% | Strong for period similarity; 66 balanced years. |
| 10 | Runtime bucket | `runtime` | 98.4% | Coarse bucket, not raw minutes. |
| 11 | Language | `original_language` | 100% | 26% non-English; cultural-similarity proxy. |
| 12 | Popularity / rating | `popularity`, `vote_average`, `vote_count` | ~97.5% | For re-ranking, **not** for the vector. 126 films unrated. |
| 13 | Tagline | `tagline` | ~81% | Sparse; useful only as a bonus text field. |

**Recommended v1 vector:** `overview` + `keywords` + `genres` + `cast` + `director` + `era`, with a `min_df ≥ 2` stopword-ish filter, stinger keywords removed, and popularity reserved for re-ranking.

**Deliberately excluded:** budget, revenue, production companies, spoken languages. Dropped at acquisition (§5) and not required by the brief. Recoverable by widening the projection.

## 13. Risks and limitations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | **Licence scope creep.** Approval covers non-commercial use only. Monetising MovieMind would breach the ToU. | **High** | Re-audit licence before any monetisation. Track it as a release blocker. |
| 2 | **6-month cache expiry.** The snapshot ages out. | **High** | Treat as disposable. Re-fetch on a ≤6-month cycle. Staff guidance ties this to the storage allowance. |
| 3 | **TMDb can change or withdraw terms at sole discretion.** | **High** | The dataset is regenerable from scratch; never treat it as a permanent asset. |
| 4 | **Residual popularity bias.** Pool-based randomisation dilutes but does not remove it. | Medium | Documented in §5. Use `vote_count`/`popularity` for weighting, not for correcting the sample. |
| 5 | **No ratings interaction data.** Recommending by similarity, not by user taste. | Medium | Honest positioning: "find films like this one", not "what to watch next". |
| 6 | **Long-tail keyword gaps** cluster in non-English titles. | Medium | Accept 90.48%; consider a wider pool or `/keywords` backfill. |
| 7 | **Co-directed films** (285) break naive single-value director features. | Medium | Model director as a set. |
| 8 | **No true duplicates, but 69 title collisions.** A future dedup pass could delete remakes. | Medium | Key on `id`; rule 1. |
| 9 | **Sparse ratings.** 126 unrated, 505 under 10 votes. | Low | Exclude from rating-derived features. |
| 10 | **Crowd-sourced accuracy.** Overviews and keywords vary in quality. | Low | Inherent to the source; acceptable for a demo. |
| 11 | **Terms staff guidance is informal**, not contractual. | Low | Recorded in §4 with its limits. |
| 12 | **Snapshot is point-in-time.** Drift in votes, popularity, keywords. | Low | Version by SHA-256; re-fetch rather than patch. |

## 14. Reproducibility instructions

**Prerequisites:** Python 3.13+, `requests`. A free TMDb account at `https://www.themoviedb.org/settings/developer`.

```powershell
# 1. Credential (never commit this file)
Copy-Item .env.example .env
notepad .env
#    TMDB_API_READ_ACCESS_TOKEN=<your v4 read access token>
# or: TMDB_API_KEY=<your v3 api key>

# 2. Verify the snapshot is intact
Get-FileHash data\raw\tmdb_movies.jsonl -Algorithm SHA256
#    expect 5b1079cd46efad0dec21f74736bebc6db6ba2f91e1b7497365c86225fd36307b

# 3. Re-run the audit (read-only, safe to repeat)
python scripts\audit_dataset.py

# 4. Regenerate the snapshot from scratch (~12 min)
Remove-Item data\raw\tmdb_movies.jsonl, data\raw\tmdb_manifest.json
python scripts\fetch_tmdb_snapshot.py --target 5000 --year-start 1960 --year-end 2025

# 5. Resume an interrupted run instead of restarting
python scripts\fetch_tmdb_snapshot.py --target 5000 --year-start 1960 --year-end 2025 --resume
```

**Determinism.** With seed `20260926` the same 5,000 IDs are selected, so a re-fetch reproduces the same film set. The SHA-256 will differ if TMDb's underlying data changed between runs — that is expected and is the signal to re-audit.

**Secret hygiene — verified, not assumed.** A throwaway repo was created, `.env` / `data/raw/` / `data/audit/` populated with canaries, and `git add -A` run. Git offered to track only `.env.example`, `.gitignore`, `data/.gitkeep`, and the two scripts. The token, the snapshot, the manifest, and the audit outputs were all excluded.

**Attribution is a build requirement, not a comment.** The notice in §4 must appear in the app UI. It is defined as a constant in `scripts/fetch_tmdb_snapshot.py` (`TMDB_REQUIRED_NOTICE`) so it travels with the data.

## 15. Final dataset decision

### A. DATASET APPROVED

**`data/raw/tmdb_movies.jsonl` — 5,000 films, 13.32 MiB, SHA-256 `5b1079cd…6307b` — is approved as MovieMind's Phase 3 dataset.**

**Grounds:**

1. **Provenance is exact.** Official TMDb API, credentialed, no mirror, no scraping, pinned by SHA-256 with a manifest recording the sampling plan.
2. **Licence permits the intended use.** Non-commercial local use is explicitly allowed, and TMDb staff have directly confirmed that a non-commercial TF-IDF recommender and stored derived vectors are acceptable. The ML clause targets *training*, which MovieMind does not do.
3. **Integrity is perfect.** 5,000/5,000 parsed, 0 malformed, 0 duplicate IDs, 0 invalid IDs, 0 true duplicate rows.
4. **Coverage comfortably exceeds the bar.** 89.86% full, 98.20% usable-core, 100% minimal. All minimum fields from Phase 1 are present at ≥90.48%.
5. **The signal is rich where it matters.** Median 42-word overviews, median 9 keywords, median 2 genres, median 20 cast, 11,914 distinct keywords, 19 genres, 66 balanced years, 1,127 named franchises.
6. **The size is a non-issue.** 13.32 MiB total.
7. **The blockers from Phase 1 are resolved.** The two open questions — provenance and licence — are now answered from primary sources, and the apparent ML/AI prohibition is resolved by staff clarification.

**Mandatory conditions (violating any one voids this approval):**

- **Non-commercial use only.** No monetisation, no ads, no paid tier, no sponsorships.
- **Attribution displayed** using the verbatim notice in §4, with the TMDb logo.
- **Never commit the data or the token** to version control. Verified in §14; re-verify before any push.
- **Never train** an ML model on TMDb content.
- **Re-fetch within 6 months** of each acquisition; treat the snapshot as disposable.
- **Re-read the terms** before any change in distribution model, monetisation, or deployment.

**Accepted trade-offs:** residual popularity bias (§13.4); 9.52% keyword gap concentrated in the long tail (§10); no user interaction data, so the product is similarity-based, not preference-based (§13.5); 5.5% of films need credits-stinger keywords stripped before vectorising (§9).

**Deferred to Phase 3 (not blocking):** the 12 cleaning rules in §11, the v1 feature set in §12, and the TF-IDF + cosine pipeline.

**Alternative if conditions are ever breached:** Wikidata (CC0, no overview/keywords) plus a separately licensed text source. This is a documented fallback, not the recommendation.

---

### Appendix A — Audit corrections log

| Date | Finding | Impact | Resolution |
| --- | --- | --- | --- |
| 2026-09-26 | `append_to_response=keywords` returns `{"keywords": [...]}`, not `{"results": [...]}` | 100% keyword loss across 5,000 films | Projection accepts both shapes; re-fetched; fill rate 90.48% |
| 2026-09-26 | Collection projected as a bare boolean | Lost franchise names on 1,127 films | Project `{id, name}`; re-fetched |
| 2026-09-26 | ASCII-only title normalisation collapsed non-Latin titles to `""` | 1 fabricated 7-film duplicate group; 1 false same-year duplicate | Unicode-aware normalisation with non-collapsing fallback; redundant rows 73 → **0** |

### Appendix B — File inventory

| Path | Tracked in git? | Purpose |
| --- | --- | --- |
| `docs/phase-01-dataset-strategy.md` | Yes | Source selection rationale |
| `docs/phase-02-dataset-audit.md` | Yes | This report |
| `scripts/fetch_tmdb_snapshot.py` | Yes | Official-API acquisition, resumable, manifest writer |
| `scripts/audit_dataset.py` | Yes | Read-only statistical audit |
| `.gitignore` | Yes | Excludes secrets and data |
| `.env.example` | Yes | Credential template, no secret |
| `data/.gitkeep` | Yes | Preserves the data directory |
| `data/raw/tmdb_movies.jsonl` | **No** | The 5,000-film snapshot |
| `data/raw/tmdb_manifest.json` | **No** | Provenance + SHA-256 |
| `data/audit/audit_summary.json` | **No** | Machine-readable audit |
| `data/audit/audit_report.md` | **No** | Human-readable audit |
| `.env` | **No** | Live credential — never commit |
