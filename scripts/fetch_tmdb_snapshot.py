#!/usr/bin/env python3
"""
Fetch a TMDb (The Movie Database) movie-metadata snapshot for MovieMind v1.

PROVENANCE
----------
Original source : The Movie Database (TMDb), via its OFFICIAL public API.
Endpoint        : https://api.themoviedb.org/3/
Data owner      : TMDb. This script does NOT scrape TMDb, and does NOT download
                  any third-party "mirror", dump or scrape of TMDb data.
Terms           : https://www.themoviedb.org/api-terms-of-use
                  Free for NON-COMMERCIAL use. Commercial use requires a separate
                  written agreement with TMDb. Attribution to TMDb is MANDATORY
                  and the required notice must be displayed in the application.

REDISTRIBUTION
--------------
TMDb's terms do not grant redistribution rights. The snapshot this script writes
is therefore NOT committed to version control (see .gitignore). It is a locally
generated, regenerable build artifact. Another developer obtains it by running
this script with their own free TMDb API key.

CREDENTIALS
-----------
Read from the environment. Never from a committed file.

    TMDB_API_READ_ACCESS_TOKEN   v4 read access token (preferred)
    TMDB_API_KEY                 v3 API key (fallback)

WHAT IS STORED
--------------
For each film, ONE request to /3/movie/{id}?append_to_response=credits,keywords
returns overview, genres, keywords, cast, crew and ratings together. The
response is then PROJECTED down to the fields MovieMind v1 needs, to keep the
snapshot small enough for a normal laptop.

Fields deliberately NOT retained (all recoverable by re-running this script,
since the snapshot is fully regenerable):
    budget, revenue, homepage, spoken_languages[], production_companies[],
    production_countries[], status, video, original_language is KEPT,
    cast beyond the first --cast-limit entries, crew beyond the retained jobs,
    adult/popularity-adjacent fields, images/posters, and all person-level
    metadata (gender, credit_id, order, known_for).

No video, audio or poster/image assets are ever downloaded.

USAGE
-----
    python scripts/fetch_tmdb_snapshot.py --target 5000
    python scripts/fetch_tmdb_snapshot.py --target 5000 --resume
    python scripts/fetch_tmdb_snapshot.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

# --- Attribution strings mandated by TMDb. Kept here so the licence obligation
# --- travels with the data and can be surfaced by the app later.
TMDB_ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
TMDB_REQUIRED_NOTICE = (
    "This [website, program, service, application, product] uses TMDB and the TMDB APIs "
    "but is not endorsed, certified, or otherwise approved by TMDB."
)
TMDB_TERMS_URL = "https://www.themoviedb.org/documentation/api/terms-of-use"
TMDB_TERMS_URL_MAIN = "https://www.themoviedb.org/terms-of-use"
TMDB_SOURCE = "https://api.themoviedb.org/3/ (official TMDb API)"

API_ROOT = "https://api.themoviedb.org/3"

# Crew jobs retained in the snapshot. Director is required by the content model;
# Screenplay/Story are kept for flexibility without materially growing the file.
RETAINED_CREW_JOBS = ("Director", "Screenplay", "Story")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "raw" / "tmdb_movies.jsonl"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "raw" / "tmdb_manifest.json"


# --------------------------------------------------------------------------- #
# credentials / http
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path) -> None:
    """
    Minimal .env loader, so a credential never has to be pasted into a shell
    history or a chat transcript. Existing environment variables always win, so
    CI can inject a secret directly without a .env file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and val and key not in os.environ:
            os.environ[key] = val


def resolve_credentials() -> tuple[str | None, str, dict[str, str]]:
    """Return (bearer_token, key_kind, auth_headers). Never logs the secret."""
    _load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("TMDB_API_READ_ACCESS_TOKEN", "").strip()
    if token:
        return token, "v4_read_access_token", {"Authorization": f"Bearer {token}"}
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if key:
        return None, "v3_api_key", {}
    sys.exit(
        "ERROR: no TMDb credential found.\n"
        "  Option 1 (recommended): create a .env file next to .gitignore containing\n"
        "      TMDB_API_READ_ACCESS_TOKEN=<your v4 read access token>\n"
        "    (copy .env.example -> .env). .env is gitignored.\n"
        "  Option 2: export the variable in your shell.\n"
        "  Register free at https://www.themoviedb.org/settings/developer"
    )


def make_session(auth_headers: dict[str, str], api_key: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "MovieMind/0.2 (educational dataset-acquisition script)",
            "Accept": "application/json",
            **auth_headers,
        }
    )
    if api_key:
        s.params = {"api_key": api_key}
    return s


def get_json(
    session: requests.Session,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = 5,
    timeout: int = 45,
) -> dict[str, Any]:
    """GET with polite backoff. Honours 429 Retry-After and TMDb's rate limits."""
    url = f"{API_ROOT}{path}"
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise
            print(f"    ! network error ({type(exc).__name__}), retry {attempt}/{max_retries}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
            continue

        if r.status_code == 200:
            return r.json()

        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", delay * 2))
            print(f"    ~ rate limited (429), sleeping {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
            delay *= 2
            continue

        if r.status_code in (401, 403, 404):
            # Not retryable. 404 simply means the id is absent from TMDb.
            return {"__error__": r.status_code, "__body__": r.text[:200]}

        if 500 <= r.status_code < 600:
            time.sleep(delay)
            delay *= 2
            continue

        return {"__error__": r.status_code, "__body__": r.text[:200]}

    raise RuntimeError(f"GET {url} failed after {max_retries} attempts")


# --------------------------------------------------------------------------- #
# candidate discovery
# --------------------------------------------------------------------------- #
def discover_ids_for_year(
    session: requests.Session,
    year: int,
    *,
    max_pages: int,
    min_pool: int,
    min_vote_count: int,
) -> list[int]:
    """
    Collect candidate movie ids for one release year via /discover/movie.

    RESIDUAL SAMPLING BIAS (documented, not hidden): discover is ordered by
    popularity descending, so any pool drawn from it over-represents popular
    films. We mitigate by walking enough pages to build a pool several times the
    quota and then sampling randomly from that pool (see build_sample_plan).
    Popularity bias therefore remains, but is diluted and measurable.
    """
    ids: list[int] = []
    seen: set[int] = set()
    for page in range(1, max_pages + 1):
        payload = get_json(
            session,
            "/discover/movie",
            {
                "language": "en-US",
                "primary_release_date.gte": f"{year}-01-01",
                "primary_release_date.lte": f"{year}-12-31",
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "include_video": "false",
                "vote_count.gte": min_vote_count,
                "page": page,
            },
        )
        results = payload.get("results") or []
        if not results:
            break
        for item in results:
            mid = item.get("id")
            if isinstance(mid, int) and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        if len(ids) >= min_pool:
            break
        time.sleep(0.25)  # be polite to the API
    return ids


def build_sample_plan(
    session: requests.Session,
    *,
    target: int,
    year_start: int,
    year_end: int,
    pages_per_window: int,
    oversample: int,
    min_vote_count: int,
    seed: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    """
    Stratified-by-release-year sample plan.

    Each year is an independent stratum. We build a pool of up to
    ``pages_per_window * 20`` ids for the year, then randomly sample a quota.
    Quotas are rebalanced afterwards so a short year (e.g. a year with few
    eligible films) does not leave the total target unmet.
    """
    rng = random.Random(seed)
    years = list(range(year_start, year_end + 1))
    n_years = len(years)
    quota = max(1, -(-target // n_years))  # ceil

    pools: dict[int, list[int]] = {}
    strata_log: list[dict[str, Any]] = []
    for year in years:
        pool = discover_ids_for_year(
            session,
            year,
            max_pages=pages_per_window,
            min_pool=quota * oversample,
            min_vote_count=min_vote_count,
        )
        pools[year] = pool
        strata_log.append({"year": year, "pool_size": len(pool), "quota": quota})
        print(f"  {year}: pool={len(pool):4d} quota={quota}")

    selected: list[int] = []
    # Pass 1 — fill each stratum's quota from its own pool.
    taken: set[int] = set()
    for year in years:
        pool = pools[year]
        quota_y = strata_log[years.index(year)]["quota"]
        k = min(quota_y, len(pool))
        chosen = rng.sample(pool, k)
        taken.update(chosen)
        selected.extend(chosen)
        strata_log[years.index(year)]["selected"] = k

    # Pass 2 — top up from the largest remaining pools if we fell short.
    if len(selected) < target:
        print(f"  topping up: {len(selected)}/{target} selected")
        leftovers: dict[int, list[int]] = {}
        for year in years:
            rest = [i for i in pools[year] if i not in taken]
            if rest:
                leftovers[year] = rest
        while len(selected) < target and leftovers:
            year = max(leftovers, key=lambda y: len(leftovers[y]))
            rest = leftovers[year]
            batch = rng.sample(rest, min(len(rest), 50))
            for i in batch:
                if i not in taken:
                    taken.add(i)
                    selected.append(i)
                    strata_log[years.index(year)]["selected"] += 1
            leftovers[year] = [i for i in rest if i not in taken]
            if not leftovers[year]:
                del leftovers[year]

    rng.shuffle(selected)
    return selected[:target], strata_log


# --------------------------------------------------------------------------- #
# fetch + projection
# --------------------------------------------------------------------------- #
def project_movie(payload: dict[str, Any], *, cast_limit: int) -> dict[str, Any] | None:
    """
    Reduce a full TMDb movie response to the MovieMind v1 record shape.

    This is a LOSSY PROJECTION performed at ACQUISITION time, not a cleaning
    step. Nothing is being corrected, deduplicated or removed for quality
    reasons here — see docs/phase-02-dataset-audit.md for cleaning rules, which
    are specified separately and are NOT applied by this script.
    """
    mid = payload.get("id")
    if not isinstance(mid, int):
        return None

    genres = [
        {"id": g.get("id"), "name": g.get("name")}
        for g in (payload.get("genres") or [])
        if isinstance(g, dict)
    ]

    kw_block = payload.get("keywords") or {}
    # append_to_response can return {"results": [...] } OR {"keywords": [...]}
    kw_list = kw_block.get("results") or kw_block.get("keywords") or []
    keywords = [
        {"id": k.get("id"), "name": k.get("name")}
        for k in kw_list
        if isinstance(k, dict)
    ]

    collection = payload.get("belongs_to_collection")

    credits = payload.get("credits") or {}
    cast = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "character": c.get("character"),
            "order": c.get("order"),
        }
        for c in (credits.get("cast") or [])[:cast_limit]
        if isinstance(c, dict)
    ]

    crew = [
        {"id": c.get("id"), "name": c.get("name"), "job": c.get("job"), "department": c.get("department")}
        for c in (credits.get("crew") or [])
        if isinstance(c, dict) and c.get("job") in RETAINED_CREW_JOBS
    ]

    return {
        "id": mid,
        "title": payload.get("title"),
        "original_title": payload.get("original_title"),
        "overview": payload.get("overview"),
        "tagline": payload.get("tagline"),
        "genres": genres,
        "keywords": keywords,
        "cast": cast,
        "crew": crew,
        "release_date": payload.get("release_date"),
        "runtime": payload.get("runtime"),
        "original_language": payload.get("original_language"),
        "status": payload.get("status"),
        "popularity": payload.get("popularity"),
        "vote_average": payload.get("vote_average"),
        "vote_count": payload.get("vote_count"),
        "adult": payload.get("adult"),
        "belongs_to_collection": (
            {"id": collection.get("id"), "name": collection.get("name")}
            if isinstance(collection, dict)
            else None
        ),
    }


def already_fetched(path: Path) -> set[int]:
    if not path.exists():
        return set()
    ids: set[int] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(int(json.loads(line)["id"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return ids


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch an official TMDb movie snapshot for MovieMind.")
    ap.add_argument("--target", type=int, default=5000, help="number of films to snapshot")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output JSONL path")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="provenance manifest path")
    ap.add_argument("--year-start", type=int, default=1960, help="first release-year stratum")
    ap.add_argument("--year-end", type=int, default=2025, help="last release-year stratum")
    ap.add_argument("--pages-per-window", type=int, default=10, help="discover pages per year")
    ap.add_argument("--oversample", type=int, default=4, help="pool multiplier vs quota")
    ap.add_argument("--min-vote-count", type=int, default=0, help="TMDb discover vote_count floor")
    ap.add_argument("--cast-limit", type=int, default=20, help="max cast entries retained per film")
    ap.add_argument("--seed", type=int, default=20260926, help="sampling seed (reproducibility)")
    ap.add_argument("--sleep", type=float, default=0.12, help="delay between detail requests (s)")
    ap.add_argument("--resume", action="store_true", help="skip ids already present in --out")
    ap.add_argument("--dry-run", action="store_true", help="report the plan and exit; no writes")
    args = ap.parse_args()

    token, key_kind, auth_headers = resolve_credentials()
    api_key = None if token else os.environ.get("TMDB_API_KEY", "").strip()
    session = make_session(auth_headers, api_key)

    # Fail fast on a bad credential rather than after 1,000 wasted calls.
    probe = get_json(session, "/configuration")
    if probe.get("__error__"):
        sys.exit(f"ERROR: TMDb rejected the credential (HTTP {probe['__error__']}). {probe.get('__body__','')}")
    print(f"credential OK (auth kind: {key_kind})")

    n_years = args.year_end - args.year_start + 1
    print(f"planning sample: target={args.target} strata={n_years} years "
          f"({args.year_start}-{args.year_end}) pages/window={args.pages_per_window} "
          f"oversample=x{args.oversample} seed={args.seed}")
    print("  (a pool several times the quota is built per year, then randomly sampled,")
    print("   to dilute discover's popularity-descending bias)")

    started = datetime.now(UTC)
    plan, strata_log = build_sample_plan(
        session,
        target=args.target,
        year_start=args.year_start,
        year_end=args.year_end,
        pages_per_window=args.pages_per_window,
        oversample=args.oversample,
        min_vote_count=args.min_vote_count,
        seed=args.seed,
    )
    print(f"plan built: {len(plan)} ids")

    if args.dry_run:
        print("dry run: no data written.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)

    done: set[int] = already_fetched(args.out) if args.resume else set()
    if done:
        print(f"resume: {len(done)} ids already present")

    fetched = skipped = errors = 0
    error_samples: list[dict[str, Any]] = []

    with args.out.open("a", encoding="utf-8") as fh:
        for n, mid in enumerate(plan, start=1):
            if mid in done:
                skipped += 1
                continue
            payload = get_json(
                session,
                f"/movie/{mid}",
                {"language": "en-US", "append_to_response": "credits,keywords"},
            )
            if "__error__" in payload:
                errors += 1
                if len(error_samples) < 20:
                    error_samples.append({"id": mid, "http": payload["__error__"]})
                continue
            rec = project_movie(payload, cast_limit=args.cast_limit)
            if rec is None:
                errors += 1
                continue
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fetched += 1
            if fetched % 250 == 0:
                print(f"  {n}/{len(plan)} planned | fetched={fetched} errors={errors}")
            time.sleep(args.sleep)

    # --- manifest: provenance + reproducibility record -----------------------
    h = hashlib.sha256()
    size_bytes = 0
    with args.out.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size_bytes += len(chunk)

    manifest = {
        "artifact": args.out.name,
        "source": TMDB_SOURCE,
        "data_owner": "The Movie Database (TMDb)",
        "terms_url": TMDB_TERMS_URL,
        "terms_url_main": TMDB_TERMS_URL_MAIN,
        "licence_summary": (
            "Free for non-commercial use via the official API. Commercial use requires a "
            "separate written agreement with TMDb. TMDb attribution is MANDATORY. "
            "No redistribution rights granted - do not commit this file to version control."
        ),
        "attribution_required": True,
        "attribution_text": TMDB_ATTRIBUTION,
        "required_notice": TMDB_REQUIRED_NOTICE,
        "credential_kind": key_kind,
        "fetch_started_utc": started.isoformat(),
        "fetch_finished_utc": datetime.now(UTC).isoformat(),
        "target": args.target,
        "records_fetched_this_run": fetched,
        "records_skipped_resume": skipped,
        "fetch_errors": errors,
        "error_samples": error_samples,
        "total_records_in_file": len(already_fetched(args.out)),
        "sha256": h.hexdigest(),
        "size_bytes": size_bytes,
        "sampling": {
            "method": "stratified by TMDb primary_release_year; per-year pool built from "
                      "/discover/movie sorted popularity.desc, then random sample without "
                      "replacement from that pool",
            "residual_bias": "over-represents popular films relative to the full TMDb "
                             "catalogue, because /discover is popularity-ordered",
            "year_start": args.year_start,
            "year_end": args.year_end,
            "pages_per_window": args.pages_per_window,
            "oversample": args.oversample,
            "min_vote_count": args.min_vote_count,
            "seed": args.seed,
            "cast_limit": args.cast_limit,
            "retained_crew_jobs": list(RETAINED_CREW_JOBS),
            "include_adult": False,
        },
        "strata": strata_log,
        "projection_note": (
            "Records are projected at acquisition to the fields MovieMind v1 needs. "
            "Dropped TMDb fields (budget, revenue, homepage, production companies/countries, "
            "spoken_languages, video, images, and person-level credit metadata) are fully "
            "recoverable by re-running this script."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n--- done ---")
    print(f"file        : {args.out}")
    print(f"size        : {size_bytes:,} bytes ({size_bytes / 1_048_576:.1f} MiB)")
    print(f"records     : {manifest['total_records_in_file']:,}")
    print(f"sha256      : {h.hexdigest()}")
    print(f"manifest    : {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
