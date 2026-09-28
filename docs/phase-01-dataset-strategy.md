# Phase 01 — MovieMind Dataset Strategy

**Status:** Complete (research only)
**Date of verification:** 2026-09-26
**Scope:** Selection of a freely available, ₹0, local-first movie metadata dataset suitable for a content-based recommender (text preprocessing → TF-IDF → cosine similarity).

> **Reading convention used throughout this document**
> - **[VERIFIED]** — confirmed directly by me in this phase (HTTP response, downloaded file inspection, or primary-source terms page).
> - **[PRIMARY-SOURCE]** — quoted/paraphrased from the dataset owner's own documentation or terms page, which I fetched.
> - **[UNVERIFIED]** — described by third parties (e.g. dataset aggregator/mirror pages) but **not** downloaded or independently confirmed by me. Treat as a lead to confirm in a later phase, not as fact.
> - **[RECOMMENDATION]** — my judgement, not a dataset fact.

---

## 1. Objective

Select **one** primary dataset for MovieMind v1 that:

1. Is freely available and costs ₹0 to obtain and run.
2. Requires no paid API, subscription, or commercial licence.
3. Supplies the text-bearing metadata a *content-based* recommender actually consumes: title, genres, overview/description, keywords/tags, cast, director/crew, and ideally ratings/popularity.
4. Can be stored locally and processed offline, consistent with MovieMind's local-first goal.
5. Has licensing/usage terms that are compatible with a non-commercial personal project.

Explicitly **not** an objective of this phase: building the recommender, writing preprocessing code, or choosing a backend/frontend.

### 1.1 A note on what "content-based" requires

A TF-IDF + cosine-similarity content-based recommender treats each movie as a bag of tokens. Cosine similarity between two such vectors is only meaningful to the extent the vocabulary is **rich and discriminative**. This makes dataset *field richness* — not dataset *size* — the binding constraint. A 10,000-movie dataset with 20 distinct genre labels and no descriptive text is a far weaker recommender input than a 100,000-movie dataset with overviews and keywords. This principle drives the recommendation in §4.

---

## 2. Dataset candidates investigated

### 2.1 MovieLens (`ml-latest`, `ml-latest-small`, `ml-25m`)

- **Source:** GroupLens Research, University of Minnesota — <https://grouplens.org/datasets/movielens/>
- **[PRIMARY-SOURCE] Approximate size** (from GroupLens dataset pages and READMEs):
  - `ml-latest`: ~33,000,000 ratings and 2,000,000 tag applications across 86,000 movies by 330,975 users. Page states "Last updated 9/2018"; its README states the export was generated 2023-07-20 with 33,832,162 ratings / 2,328,315 tag applications / 86,537 movies / 330,975 users.
  - `ml-latest-small`: 100,000 ratings, 3,600 tag applications, 9,000 movies, 600 users. Last updated 9/2018.
  - `ml-25m`: 25,000,095 ratings and 1,093,360 tag applications across 62,423 movies by 162,541 users. Released 12/2019.
- **[VERIFIED] Download availability and exact size** (HTTP `HEAD` on 2026-09-26):

  | File | HTTP | Bytes | MB |
  |---|---:|---:|---:|
  | `ml-latest-small.zip` | 200 | 978,202 | 0.9 |
  | `ml-latest.zip` | 200 | 350,896,731 | 334.6 |
  | `ml-25m.zip` | 200 | 261,978,986 | 249.8 |

- **[PRIMARY-SOURCE] Available fields** — files: `movies.csv`, `ratings.csv`, `tags.csv`, `links.csv`, `genome-scores.csv`, `genome-tags.csv`.
  - `movies.csv` → `movieId, title, genres`. Titles are "entered manually or imported from themoviedb.org" and include the release year in parentheses; the README warns "Errors and inconsistencies may exist in these titles."
  - `genres` → pipe-separated, drawn from a **closed list of 19 genres** (Action, Adventure, Animation, Children's, Comedy, Crime, Documentary, Drama, Fantasy, Film-Noir, Horror, Musical, Mystery, Romance, Sci-Fi, Thriller, War, Western) plus the literal `(no genres listed)`.
  - `ratings.csv` → `userId, movieId, rating, timestamp` (5-star, 0.5 increments).
  - `tags.csv` → `userId, movieId, tag, timestamp`; free-text, user-authored, "typically a single word or short phrase".
  - `links.csv` → `movieId, imdbId, tmdbId`.
  - Tag Genome → `genome-scores.csv` (`movieId, tagId, relevance`) and `genome-tags.csv` (`tagId, tag`); relevance scores computed by a machine-learning algorithm over user tags, ratings and reviews.
- **[VERIFIED] Missing data — measured locally.** I downloaded and analysed `ml-latest-small` (0.9 MB) rather than assume:

  | Metric | Value |
  |---|---:|
  | Rows in `movies.csv` | 9,742 |
  | Columns in `movies.csv` | `movieId, title, genres` |
  | Movies with `(no genres listed)` | 34 (0.35%) |
  | **Distinct genre tokens in the whole corpus** | **20** |
  | Rows in `tags.csv` | 3,683 |
  | **Distinct movies with ≥1 tag** | **1,572 (16.14%)** |
  | Distinct tag strings | 1,589 |
  | Mean tags per tagged movie | 2.34 |
  | Movies with exactly 1 tag | 1,014 |
  | Movies with ≥3 tags | 320 |
  | Movies with genres **and** ≥1 tag | 1,571 (16.13%) |
  | `links.csv` rows | 9,742 |
  | `links.csv` with `imdbId` populated | 9,742 (100%) |
  | `links.csv` with `tmdbId` populated | 9,734 (99.92%) |
  | `links.csv` with both | 9,734 (99.92%) |

  Genre distribution is heavily skewed — top 12: Drama 4,361; Comedy 3,756; Thriller 1,894; Action 1,828; Romance 1,596; Adventure 1,263; Crime 1,199; Sci-Fi 980; Horror 978; Fantasy 779; Children's 664; Animation 611.
  Top tags are `In Netflix queue` (131), `atmospheric` (41), `surreal` (24), `superhero` (24), `funny` (24), `thought-provoking` (24), `sci-fi` (23), `Disney` (23), `religion` (22), `quirky` (22), `dark comedy` (21), `suspense` (21).

- **Fields useful for MovieMind:** `title`, `genres`, `tags` (sparse, noisy), `links.csv` (excellent cross-reference to IMDb/TMDb IDs), and — only if a future phase adds collaborative signals — `ratings.csv`.
- **Fields MovieMind v1 needs but MovieLens does not have:** `overview`, `keywords` (curated), `cast`, `director`, `crew`. There is **no** overview/description field anywhere in the dataset.
- **[PRIMARY-SOURCE] Licensing / usage:** GroupLens usage licence — no endorsement may be claimed; use must be acknowledged in publications; commercial or revenue-bearing use requires prior permission from a GroupLens faculty member. Redistribution terms differ by release: `ml-latest` and `ml-latest-small` permit redistribution "including transformations... under these same license conditions", whereas `ml-25m` and `ml-10m` state "The user may not redistribute the data without separate permission." GroupLens also states they "typically do not permit public redistribution". The `links.csv` file warns that use of IMDb/TMDb IDs "is subject to the terms of each provider."
- **Advantages:** genuinely free and stable; the canonical MovieLens benchmark; no API key; no network needed at runtime; small `latest-small` variant enables fast iteration; 100% IMDb-ID cross-referencing in the sample I measured.
- **Limitations:** **fatal for v1 as a sole source** — no overview, no keywords, no cast, no director/crew; genre vocabulary limited to 19 fixed labels; tags cover only ~16% of the catalogue with a 2.34-tag mean and include non-descriptive noise such as `In Netflix queue`; a 5-star rating column is useless to a pure content-based model.
- **Suitable for MovieMind v1?** **No, not as the primary dataset.**

---

### 2.2 TMDB-derived movie metadata datasets

- **Source:** The Movie Database (TMDb) metadata, either obtained through the free TMDb API or via third-party static dumps mirrored on Kaggle/Hugging Face.
- **[UNVERIFIED] Approximate size.** Mirror/aggregator pages describe dumps ranging from ~4,800 movies (the classic "TMDB 5000") to ~700,000 ("Movies Daily Update Dataset") to ~960,000–1,000,000 ("The Ultimate 1 Million Movies Dataset (TMDB + IMDb)"). I did **not** download any of these, so treat these counts as leads to confirm.
- **[PRIMARY-SOURCE / VERIFIED-from-docs] Available fields.** The TMDb *movie* object and its companion *credits* and *keywords* endpoints supply: `id`, `title`, `original_title`, `overview`, `tagline`, `genres[]`, `keywords[]`, `cast[]` (with `character`), `crew[]` (with `job`/`department`, from which `Director` is extractable), `release_date`, `runtime`, `original_language`, `spoken_languages[]`, `production_companies[]`, `production_countries[]`, `budget`, `revenue`, `status`, `popularity`, `vote_average`, `vote_count`. The long-standing "TMDB 5000" CSV pair (`tmdb_5000_movies.csv`, `tmdb_5000_credits.csv`) contains exactly this shape — the Kaggle description lists `homepage, id, original_title, overview, popularity, production_companies, production_countries, release_date, spoken_languages, status, tagline, vote_average` among its columns.
- **Fields useful for MovieMind:** **all six target fields are present in one coherent source** — title, genres, overview, keywords, cast, director, plus `vote_average`/`popularity`.
- **Missing-data concerns:** TMDb fields are crowd-sourced; Kaggle's own description warns "All fields are filled out by users so don't expect them to agree on keywords, genres, ratings, or the like." `overview` and `keyword` arrays are empty for a long tail of obscure titles, so completeness must be measured per field after download, not assumed.
- **[PRIMARY-SOURCE] Licensing / usage — this is the critical risk.** TMDb's API Terms of Use state that the API is free for **non-commercial** use provided TMDb is attributed, and that **any commercial use requires a separate written agreement** (which "may be subject to, among other things, payment of fees"). Mandatory attribution includes using the TMDb logo and displaying verbatim: *"This [website, program, service, application, product] uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."* The FAQ confirms free non-commercial use "as long as you attribute TMDB as the source of the data and/or images."
  - **Complication worth flagging honestly:** community mirrors on Kaggle self-declare licences such as *CC0: Public Domain*, *Apache 2.0*, or *Other*, but the underlying content belongs to TMDb. A re-uploader's licence label is not a grant from TMDb. MovieMind should treat these dumps as **"free for non-commercial use, TMDb attribution required"**, not as public-domain data.
  - **Relevant history:** the original IMDb-sourced "TMDB 5000 Movie Dataset" on Kaggle was replaced following "a DMCA takedown request from IMDB" — evidence that scraped/mirrored film metadata carries real takedown risk, and a reason to prefer the sanctioned API path or a dump sourced in compliance with TMDb's terms.
- **Advantages:** the only candidate covering the full required field set; richest free text signal (`overview` + `keywords`) for TF-IDF; stable numeric `id`; `vote_average`/`popularity` available for display and later re-ranking; widely used, so tooling examples exist.
- **Limitations:** licence is non-commercial-only and attribution is mandatory; mirror dumps are large and re-uploaded without a verifiable provenance chain; `overview`/`keywords` sparsity is unmeasured; the API path needs a (free) key and network access at build time.
- **Suitable for MovieMind v1?** **Yes — this is the recommended primary dataset**, subject to the licence handling in §7 and the field-completeness measurement deferred to Phase 2.

---

### 2.3 IMDb non-commercial datasets

- **Source:** <https://datasets.imdbws.com/> — documented at <https://developer.imdb.com/non-commercial-datasets/>
- **[VERIFIED] Download availability and exact size** (HTTP `HEAD` on 2026-09-26; the page states the data is refreshed daily):

  | File | HTTP | Bytes | MB |
  |---|---:|---:|---:|
  | `title.basics.tsv.gz` | 200 | 227,603,943 | 217.1 |
  | `title.crew.tsv.gz` | 200 | 83,275,580 | 79.4 |
  | `title.principals.tsv.gz` | 200 | 784,043,264 | 747.7 |
  | `name.basics.tsv.gz` | 200 | 310,367,414 | 296.0 |
  | `title.ratings.tsv.gz` | 200 | 8,658,989 | 8.3 |

- **[PRIMARY-SOURCE] Available fields:** gzipped TSV, UTF-8, `\N` denotes null.
  - `title.basics` → `tconst, titleType, primaryTitle, originalTitle, isAdult, startYear, endYear, runtimeMinutes, genres` (**up to three** genres).
  - `title.crew` → `tconst, directors[], writers[]`.
  - `title.principals` → `tconst, ordering, nconst, category, job, characters` (principal cast/crew per title).
  - `name.basics` → `nconst, primaryName, birthYear, deathYear, primaryProfession[], knownForTitles[]`.
  - `title.ratings` → `tconst, averageRating, numVotes`.
- **Fields useful for MovieMind:** title, year, runtime, up to 3 genres, directors, principal cast, and genuine crowd `averageRating`/`numVotes`.
- **Missing-data concerns:** `'\N'` used pervasively; `endYear` is `\N` for all non-TV types. Critically, **there is no plot/overview field and no keywords field in any of these files.** IMDb reserves plot summaries and keywords for its commercial licensing products, so the richest text signals are simply absent.
- **[PRIMARY-SOURCE] Licensing / usage — disqualifying for v1.** IMDb's help article "Can I use IMDb data in my software?" permits limited non-commercial use only on conditions that include: acknowledging the source with the statement *"Information courtesy of IMDb (https://www.imdb.com). Used with permission."*; taking data **only** from the published datasets (scraping is prohibited); and — the decisive clause — the data *"can only be used for personal and non-commercial use and must not be altered/republished/resold/repurposed to create any kind of online/offline database of movie information (except for individual personal use)."* IMDb also reserves the right to withdraw permission at its discretion.
  MovieMind's stated goal is a local-first web application that stores and displays a curated movie catalogue. That is precisely "an offline database of movie information". Even for a personal, non-commercial, ₹0 project, this clause is squarely in tension with the intended use, and the permission is revocable at IMDb's sole discretion.
- **Advantages:** largest, most authoritative and most stable free film dataset; genuine ratings with vote counts; complete director/principal-cast coverage; no API key.
- **Limitations:** the licence clause above; no overview and no keywords (the two highest-value TF-IDF inputs); requires a multi-table join across ~1.3 GB of downloads to assemble even a basic record; 3-genre cap is coarser than a keyword set.
- **Suitable for MovieMind v1?** **No.** Rejected on licensing grounds, and independently weak on the text fields that make content-based filtering work.

---

### 2.4 Wikidata

- **Source:** <https://www.wikidata.org/> / Query Service at <https://query.wikidata.org/>
- **[PRIMARY-SOURCE] Licensing:** released under a **CC0 1.0 Universal public-domain waiver** — the cleanest licence of any candidate, explicitly permitting commercial use, redistribution and derivative works with no attribution requirement.
- **Available fields** **[PRIMARY-SOURCE for properties; UNVERIFIED for coverage]:** relevant properties include genre `P136`, cast member `P161`, director `P57`, screenwriter `P58`, publication date `P577`, IMDb ID `P345`, TMDb ID `P4947`, original language `P364`, and country of origin `P495`.
- **Fields useful for MovieMind:** title/label, genres, cast, director, release date, language — plus the useful property of carrying both IMDb and TMDb IDs natively, making it a strong *linking* hub.
- **Missing-data concerns:** no plot overview and no keyword/tag field in the native schema. Statement completeness is uneven — coverage of mainstream commercial films is good, but long-tail and non-English titles are patchy, and labels/descriptions are language-dependent. Extraction requires either the public SPARQL endpoint (rate-limited, not suited to bulk extraction) or the full JSON dump.
- **Advantages:** CC0 — zero licence friction, no attribution obligation, no revocable permission; excellent entity linking.
- **Limitations:** no overview/keywords, so it must be *joined* to another source to feed a TF-IDF pipeline; uneven completeness; bulk access is either rate-limited (SPARQL) or heavyweight (full dump — one secondary report puts it at ~93 GB, **[UNVERIFIED]**).
- **Suitable for MovieMind v1?** **No, not as the primary source** — it lacks the free-text fields. Retained as the leading candidate for a *future* ID-resolution or genre-normalisation helper.

---

### 2.5 Considered and set aside

| Option | Reason set aside |
|---|---|
| OMDb API | Free tier exists but is rate-limited and requires a key; commercial use needs a paid plan. Introduces a runtime API dependency into a local-first app. |
| Kitsu / anime-focused APIs | Narrow domain scope (mostly anime/Asian cinema); weaker fit for a general movie catalogue. |
| Cineplex / Fandango / Rotten Tomatoes scrapes | Scraping is expressly disallowed by these sites' terms; legally and operationally fragile. |
| The Movie Database live API as a *runtime* dependency | Free and non-commercial, but a v1 local-first app should not need the network to render recommendations. Viable as a **build-time** snapshot source only. |
| MovieLens ratings as a collaborative signal | Out of scope: v1 is specified as content-based. |

---

## 3. Dataset comparison

| Criterion | MovieLens (`ml-latest`) | TMDB metadata | IMDb non-commercial | Wikidata |
|---|---|---|---|---|
| Cost | Free **[V]** | Free, non-commercial **[PS]** | Free, non-commercial **[PS]** | Free, CC0 **[PS]** |
| Catalogue size | 86,537 movies **[PS]** | ~5k–1M depending on artefact **[UV]** | ~1M+ titles (all types) **[PS]** | Very large **[UV]** |
| Title | Yes **[V]** | Yes **[PS]** | Yes **[PS]** | Yes **[PS]** |
| Genres | Yes — **19 fixed labels** **[V]** | Yes **[PS]** | Yes — **max 3** **[PS]** | Yes **[PS]** |
| Overview / plot | **No** **[V]** | **Yes** **[PS]** | **No** **[PS]** | **No** (native) **[PS]** |
| Keywords / tags | User tags, **16% coverage**, 2.34 mean **[V]** | **Yes** **[PS]** | **No** **[PS]** | **No** (native) **[PS]** |
| Cast | **No** **[V]** | **Yes** **[PS]** | Yes (principals) **[PS]** | Yes **[PS]** |
| Director / crew | **No** **[V]** | **Yes** **[PS]** | **Yes** **[PS]** | Yes **[PS]** |
| Ratings / popularity | Yes (per-user) **[V]** | `vote_average`, `popularity` **[PS]** | `averageRating`, `numVotes` **[PS]** | Sparse **[UV]** |
| Stable cross-ref IDs | Yes — 100% IMDb, 99.92% TMDb in sample **[V]** | Native `id` **[PS]** | Native `tconst` **[PS]** | `P345` + `P4947` **[PS]** |
| Local / offline friendly | Excellent **[V]** | Good (snapshot) **[PS]** | Good **[V]** | Poor (SPARQL or huge dump) **[UV]** |
| Licence risk for a non-commercial local app | Low **[PS]** | **Medium** — attribution mandatory, commercial use restricted **[PS]** | **High** — no republishing / no movie database **[PS]** | **None** (CC0) **[PS]** |

### 3.1 The decisive comparison

Only **TMDB metadata** supplies all six of the target fields from one source. The two strongest competitors each fail on a different axis:

- **MovieLens** fails on *field richness*. I measured this directly: 20 distinct genre tokens and no description, keyword, cast or crew field at all. A TF-IDF vector built from it would have a vocabulary of roughly 20–1,600 mostly-noisy tokens, of which only 20 are structural. Cosine similarity over that space mostly re-ranks movies by genre frequency.
- **IMDb** fails on *both* richness (no overview, no keywords) and licence (no movie database, even personally).

---

## 4. Recommended dataset

> ### **[RECOMMENDATION] MovieMind v1 uses a single dataset: TMDb movie metadata — a local snapshot of `movies` + `credits` + `keywords`.**
>
> **Not** a combination of datasets. See §5.3 for why combining is deferred.

### 4.1 Technical reasoning

1. **It is the only candidate that satisfies the brief's field list outright.** The brief asks for title, genres, overview/description, keywords/tags, cast, director/crew, and ratings/popularity. TMDb provides all seven. Every other candidate forces us to drop at least two, or to merge sources.

2. **It maximises the discriminative power of the TF-IDF stage, which is the actual bottleneck.** A content-based recommender's quality is governed by vocabulary richness and per-item token diversity. `overview` supplies natural-language prose unique to each film; `keywords` supplies curated descriptors ("time loop", "underwater", "heist"); `cast` and `director` supply high-signal categorical tokens where co-occurrence is genuinely meaningful. MovieLens's 20 genre labels cannot compete with this on any axis.

3. **It keeps the pipeline single-source and therefore auditable.** One schema, one ID space, one licence obligation, one join key (`id`). A single-source design means every field's provenance and licence is unambiguous — valuable given that the licence situation (§7) is the main risk in this phase.

4. **It satisfies the ₹0 and local-first constraints.** The TMDb API is free for non-commercial use. Taking a **one-time build-time snapshot** and serving the app entirely from local storage keeps the runtime offline, with no API key needed at runtime and no third-party service in the request path.

5. **It scales the recommendation quality with corpus size.** Because a snapshot can be taken at whatever scale the download budget allows, v1 can start with a few thousand well-populated films and grow toward the full catalogue without changing the schema — unlike MovieLens, where the catalogue is fixed at export time and cannot be extended.

### 4.2 What is explicitly *not* being recommended

- **Not MovieLens as the primary dataset.** It is the right tool for a *collaborative* recommender and the wrong tool for a *content-based* one. If MovieMind ever adds collaborative filtering, MovieLens becomes valuable again — as a separate, additive dataset, not a replacement.
- **Not IMDb.** Licensing, plus missing text.
- **Not Wikidata as primary.** CC0 is attractive, but without overview/keywords it cannot drive TF-IDF alone.

---

## 5. Required fields

### 5.1 Minimum viable field set

This is the set genuinely required for a TF-IDF + cosine content-based recommender.

| # | Field | Required? | Role in the recommender |
|---|---|---|---|
| 1 | `id` | **Yes** | Stable primary key; join key across snapshot files; de-duplication key |
| 2 | `title` | **Yes** | Display; contributes tokens; disambiguation for humans |
| 3 | `overview` | **Yes** | Richest per-item free text; primary TF-IDF signal |
| 4 | `genres[]` | **Yes** | Structured categorical tokens; coarse but high-precision filter |
| 5 | `keywords[]` | **Yes** | Curated descriptors; the highest-signal categorical block |
| 6 | `cast[]` (names) | **Yes** | Strong similarity signal; "people who like X also appear in Y" |
| 7 | `crew[]` filtered to `job == "Director"` | **Yes** | Authorial signal; strong for auteur/genre clustering |
| 8 | `release_date` / year | **Yes** | Decades/eras are a genuine preference axis; also needed to exclude unreleased titles |
| 9 | `vote_average`, `vote_count` | Recommended | Display and tie-breaking / re-ranking. **Not used in the vector.** |
| 10 | `popularity` | Recommended | Display and default ordering. **Not used in the vector.** |
| 11 | `original_language` | Recommended | Useful for filtering; low value as a similarity token |
| 12 | `runtime`, `original_title`, `tagline` | Optional | Marginal; include if free |

**The hard minimum is fields 1–8.** Fields 9–12 improve presentation and filtering but the recommender does not consume them.

### 5.2 On ratings and popularity

**[RECOMMENDATION]** Keep `vote_average`/`popularity` **out of the TF-IDF document entirely.** A rating is a number, not descriptive text; injecting it as a token would let a movie's 7.2-vs-7.3 rating dominate its thematic similarity. Retain the columns for display and for later re-ranking, which keeps the door open for hybrid ranking without contaminating v1.

### 5.3 Do we need to combine datasets? — No.

**[RECOMMENDATION]** Do **not** combine datasets for v1. Three findings drive this:

1. **MovieLens adds no field the recommender needs.** Its unique content is `ratings.csv` (collaborative signal, out of scope) and `tags.csv` (16% coverage, 2.34 tags/movie, includes noise like `In Netflix queue`). Neither is required by TF-IDF.
2. **Combining would make the weakest data the binding constraint.** Merging on `links.csv`/`tmdbId` would be technically easy — I verified 99.92% TMDb-ID coverage in the sample — but it would blend a 20-token genre vocabulary into a rich text corpus and force a weighting decision (how much should sparse, noisy tags count?) that is a *modelling* decision, not a data decision. Taking that on now is exactly the premature complexity to avoid.
3. **Each added source multiplies licence obligations.** See §7: MovieLens adds a non-commercial + attribution obligation on top of TMDb's, and IMDb is effectively unusable (§2.3). One source is one obligation.

**Revisit combining only if**, in a later phase, measurement shows the chosen snapshot's overview/keyword coverage is too thin — and then prefer *enrichment* (fetching missing fields for known TMDb IDs) over *merging* (joining a second catalogue).

---

## 6. Data risks and limitations

Risks are ordered by expected impact on v1 quality.

| # | Risk | Likelihood | Impact | Mitigation (deferred to Phase 2+) |
|---|---|---|---|---|
| R1 | **Sparse `overview`** — empty for a long tail of films | High | High | Measure per-field completeness on download; define a minimum token count; drop or backfill items below it |
| R2 | **Sparse `keywords[]`** — often empty for obscure titles | High | High | Same as R1; treat genres as the guaranteed floor |
| R3 | **Field disagreement** — crowd-sourced genres/keywords are inconsistent | High (documented) | Medium | Accept for v1; genres are coarse enough to tolerate noise |
| R4 | **Only 20–25 distinct genres → low vocabulary from that field alone** | Certain | Medium | Do not rely on genres alone; they are one block among several |
| R5 | **Long tail of near-duplicate vector documents** — remakes, franchise entries, similar-era same-studio films | Medium | Medium | Expected behaviour for content-based; mitigate later via re-ranking on `popularity`/`vote_average` |
| R6 | **Non-deterministic snapshot** — the source changes daily, so results are not reproducible | High | Medium | **Version and checksum the snapshot**; record the export date in the repo |
| R7 | **Licence risk** — TMDb terms are non-commercial + attribution-mandatory, and mirrors self-declare incompatible licences (e.g. CC0) | Medium | **High** | See §7. This is the top risk to resolve before anything ships |
| R8 | **Multi-file join** — `movies` + `credits` + `keywords` must be assembled on `id` | Certain | Low–Medium | Straightforward merge; credits and keywords are one-to-many and need flattening |
| R9 | **Unverified dump characteristics** — the ~700k/~1M mirror figures were **not** independently confirmed | Certain | Medium | Confirm the actual artefact in Phase 2 before committing |
| R10 | **Duplicate / re-release titles** — same film under multiple IDs | Medium | Low | De-duplicate on `original_title` + year |
| R11 | **Adult content present** | Medium | Low–Medium | Filter using available flags/rating data in a later phase |

**Non-risks worth recording:** no paid service, no API key at runtime, no network dependency at runtime, no user data or accounts involved.

---

## 7. Licensing and usage notes

Facts below are **[PRIMARY-SOURCE]**, taken from each owner's own terms page.

### 7.1 TMDb (the recommended source) — obligations, not permissions

- Free for **non-commercial** use; **commercial use requires a separate written agreement**, which "may be subject to, among other things, payment of fees".
- **Mandatory attribution:** use the TMDb logo (less prominent than the product's own branding, not implying endorsement) and display prominently, verbatim: *"This [website, program, service, application, product] uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."* The FAQ further specifies the attribution belongs in an "About" or "Credits" section.
- **[RECOMMENDATION]** MovieMind should: (a) remain strictly non-commercial — consistent with ₹0 and local-first; (b) render the required notice and TMDb attribution in the app's About/Credits area; (c) treat any Kaggle mirror as **"free for non-commercial use, TMDb attribution required"** rather than as the CC0/Apache label the mirror's uploader applied; (d) prefer the sanctioned API path for the snapshot, or a mirror whose provenance is documented.

### 7.2 MovieLens — if used later

- Free for research/non-commercial; no endorsement claim; acknowledgement required in publications.
- **Commercial or revenue-bearing use requires prior permission** from a GroupLens faculty member.
- Redistribution: `ml-latest`/`ml-latest-small` allow redistribution (incl. transformations) under the same conditions; `ml-25m` requires separate permission. GroupLens "typically" does not permit public redistribution — so **do not commit the raw data to the repository**.
- Cross-referenced IMDb/TMDb IDs are "subject to the terms of each provider" — another reason a MovieLens+TMDb join inherits TMDb's obligations.

### 7.3 IMDb — effectively unusable for MovieMind

- Attribution string required: *"Information courtesy of IMDb (https://www.imdb.com). Used with permission."*
- Data must come only from the published datasets; scraping prohibited.
- **"must not be altered/republished/resold/repurposed to create any kind of online/offline database of movie information (except for individual personal use)"** — directly at odds with MovieMind's purpose.
- IMDb reserves the right to withdraw permission at its discretion. Commercial licensing is sold separately via AWS Data Exchange.

### 7.4 Wikidata — no obligations

- **CC0 1.0 Universal.** No attribution required, commercial use and redistribution permitted. The cleanest option should MovieMind ever need ID resolution or genre normalisation.

### 7.5 Not legal advice

**[RECOMMENDATION]** These notes summarise published terms for engineering planning. They are not legal advice; if MovieMind ever becomes commercial or publicly hosted, re-verify the terms directly with TMDb and GroupLens at that point.

---

## 8. Proposed preprocessing implications

**Scope note:** these are implications the dataset choice creates. The actual preprocessing design belongs to a later phase.

### 8.1 Document assembly

- Build **one text document per movie** from fields 3–7 (§5.1): `overview` + `keywords[]` + `genres[]` + top-N `cast[]` names + `director` name.
- **[RECOMMENDATION]** Use **separate TF-IDF feature blocks** (or `ColumnTransformer` with per-block weighting) rather than one flat string, so that a prolific actor or a boilerplate phrase in overviews cannot dominate the vector. Field-weighting is the main quality lever the dataset choice buys us.
- Normalise the `credits` one-to-many rows into a flat per-movie cast/director list, and the `keywords` one-to-many rows into a flat keyword list, both keyed on `id`.
- Exclude fields 9–12 from the document (§5.2).

### 8.2 Text preprocessing

- Lowercase; strip punctuation; collapse whitespace; handle `Sci-Fi`/`Film-Noir` style tokens deliberately (hyphen splitting choice must be fixed and documented, since it changes the vocabulary).
- **Name handling:** apply the same normalisation to person names, and consider dropping very common surnames/forenames that add no signal.
- **Stopwords:** a standard English stopword list for `overview`; a **reduced or empty** stopword list for `keywords`/`genres`, where terms like "love" or "war" are genuinely informative.
- `overview` may be empty — fall back to `tagline` + `genres` + `keywords` so the document is never empty (see R1).

### 8.3 Vectorisation

- `TfidfVectorizer` with sublinear TF and L2 normalisation, which makes cosine similarity a plain dot product — the standard, efficient choice.
- `min_df` should be set meaningfully: with a large catalogue, pruning rare tokens removes most of the long-tail noise that `keywords` would otherwise inject.
- Vocabulary will be in the tens of thousands with `overview` present, versus ~20–1,600 for MovieLens (§2.1). **This is the single most important quantitative argument for the recommendation.**
- Expected cost for ~100k–1M movies × sparse vectors is manageable but should be measured; expect the pairwise similarity matrix to be the memory bottleneck, motivating a top-k neighbour search rather than a dense matrix.

### 8.4 Filtering before vectorising

- Drop rows with no `release_date`, non-movie entries, and (later) adult titles.
- Apply the R1/R2 minimum-completeness filter; log the drop rate — it is the honest measure of dataset quality.
- Snapshot the dataset to a **local, versioned, checksummed artifact** at ingest so the recommender is reproducible despite R6.

---

## 9. Decision summary

| Item | Decision |
|---|---|
| **Primary dataset (v1)** | **TMDb movie metadata** — local snapshot of `movies` + `credits` + `keywords` |
| **Acquisition** | One-time build-time snapshot via the free TMDb API, or an equivalently-licensed static dump. Runtime stays fully offline. |
| **Single or combined?** | **Single.** No second dataset in v1. |
| **Required fields** | `id`, `title`, `overview`, `genres[]`, `keywords[]`, `cast[]`, `Director`, `release_date` (hard minimum); plus `vote_average`, `vote_count`, `popularity` for display/re-ranking only |
| **Ratings/popularity in the vector?** | **No** — display and re-ranking only |
| **MovieLens role** | **None in v1.** Reconsider only if a later phase adds collaborative filtering |
| **IMDb role** | **Excluded** — licence prohibits creating a movie database; also lacks overview/keywords |
| **Wikidata role** | **None in v1.** Candidate for future ID resolution / genre normalisation (CC0) |
| **Cost** | ₹0. No paid API, subscription or commercial licence. |
| **Licence posture** | Non-commercial only; TMDb attribution + required notice displayed in-app; raw data not redistributed publicly |
| **Biggest open risk** | R7 — licence/attribution handling, and the unverified provenance of community mirrors |
| **Blocking prerequisite before build** | Confirm the exact snapshot artefact and measure per-field completeness (R1/R2) |

**One-line rationale:** it is the only free, non-commercial, single-source dataset that supplies every field a TF-IDF content-based recommender needs, and it turns the vocabulary from MovieLens' ~20 genre labels into tens of thousands of discriminative tokens.

---

## 10. Explicitly out of scope for Phase 1

The following were **not** done and must not be inferred from this document:

1. **No recommendation engine.** No TF-IDF pipeline, no vectoriser, no cosine-similarity computation, no scoring or ranking code.
2. **No preprocessing implementation.** §8 is analysis of implications only — no cleaning scripts, tokenisers or config files.
3. **No model or evaluation.** No model selection, no hyper-parameters, no precision/recall, no offline metrics, no A/B design.
4. **No backend.** No API, server, database schema, or service layer.
5. **No frontend.** No UI, no components, no styling — including the "polished web interface" mentioned in the project goal.
6. **No dataset download at scale.** Only `ml-latest-small.zip` (0.9 MB) was downloaded, purely to measure missing-data rates. All other sizes were confirmed via HTTP `HEAD` only.
7. **No architecture decisions.** Storage format, index type, packaging, framework and deployment are all undecided.
8. **No product decisions.** Scope of v1 features, audience, and positioning remain undefined.
9. **No multi-dataset integration work.** No join, no entity resolution, no schema mapping.
10. **No `git init`.** The repository is not currently under version control; that was not requested and has not been done.
11. **No performance benchmarking.** Feasibility of the vectoriser at scale is asserted as an expectation to measure, not a measured result.
12. **No image/posters, no user accounts, no watch history, no collaborative filtering** — none of these are part of a v1 content-based system and none were considered beyond noting their absence.

---

## Appendix A — Verification log

| What | How | Result |
|---|---|---|
| Repository state | `Get-ChildItem -Force` on `D:\MovieMind` | Directory exists, **0 items**, not a git repository |
| MovieLens availability + size | HTTP `HEAD` on `files.grouplens.org` (2026-09-26) | All three: HTTP 200; 978,202 / 350,896,731 / 261,978,986 bytes |
| MovieLens field structure | Fetched `ml-latest-README.html` and `ml-25m-README.html` | Schemas, 19-genre list, row counts confirmed |
| MovieLens **missing-data rates** | **Downloaded `ml-latest-small.zip` (0.9 MB) and computed the §2.1 table** | 9,742 movies; 20 genre tokens; 16.14% tag coverage; 2.34 tags/tagged movie; 100% IMDb / 99.92% TMDb links |
| IMDb availability + size | HTTP `HEAD` on `datasets.imdbws.com` (2026-09-26) | All five: HTTP 200; 217.1 / 79.4 / 747.7 / 296.0 / 8.3 MB |
| IMDb fields | Fetched `developer.imdb.com/non-commercial-datasets/` | Schemas confirmed; no overview/keyword field exists |
| IMDb licence | Fetched IMDb Help "Can I use IMDb data in my software?" + Conditions of Use | "must not be ... repurposed to create any kind of online/offline database of movie information" |
| MovieLens licence | Fetched GroupLens dataset pages + README licence sections | Non-commercial; redistribution terms differ per release |
| TMDb licence | Web search of `themoviedb.org/api-terms-of-use` + developer FAQ | Non-commercial free; mandatory logo + verbatim notice; commercial needs written agreement |
| Kaggle TMDB dataset sizes | Web search of Kaggle dataset pages | **UNVERIFIED** — ~4.8k / ~700k / ~960k–1M figures come from mirror descriptions only |
| Kaggle TMDB provenance history | Web search result for `tmdb/tmdb-movie-metadata` | Original version replaced after "a DMCA takedown request from IMDB" |
| Wikidata licence | Web search of Wikimedia Enterprise + community sources | CC0 1.0 Universal |
| TMDB daily ID exports | HTTP `HEAD` on `files.tmdb.org` | **403 Forbidden** — placeholder `MM_DD_YYYY` filename; not a valid test, so TMDB export availability remains **unverified** |

### Unresolved questions

1. **Which exact TMDB artefact will v1 use** — the API, or which specific static dump? Depends on the completeness measurement in Phase 2 and on resolving R7.
2. **What is the actual `overview`/`keywords` fill rate** in that artefact? R1/R2 are the highest-impact risks and remain unmeasured.
3. **Does the chosen dump's declared licence survive scrutiny**, given mirrors self-label as CC0/Apache while the content is TMDb's? Needs a deliberate decision, recorded.
4. **What catalogue size target is appropriate** for v1 — e.g. 10k well-populated films, or the full dump? Depends on download budget and memory profiling (deferred).
5. **How will `genres` be weighted** relative to `keywords` and `overview`? A modelling decision, deliberately deferred.
6. **Is `ml-latest` or `ml-latest-small` the right MovieLens artefact** if collaborative filtering is ever added? `ml-latest` has broader tag coverage; exact tag coverage was not measured for the 335 MB file.
7. **Does "₹0, local-first" definitively exclude any future hosted or monetised deployment?** The licence posture in §7 assumes strictly personal, non-commercial use.

### Note on the temporary verification artefact

`ml-latest-small.zip` was downloaded to the OS temp directory (`%TEMP%\opencode\mls\`) and expanded there for analysis. **Nothing was written into the project repository**, and no dataset files are committed.
