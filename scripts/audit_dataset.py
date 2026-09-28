#!/usr/bin/env python3
"""
Audit a TMDb movie snapshot (JSONL) for MovieMind v1.

READ-ONLY BY DESIGN. This script NEVER modifies, deduplicates or deletes the
source snapshot. It reports what is wrong and how much of it there is. Proposed
cleaning rules live in docs/phase-02-dataset-audit.md section 11 and are NOT
applied here, so that the raw audit numbers stay honest and reproducible.

Outputs
-------
  <out-dir>/audit_summary.json    machine-readable statistics
  <out-dir>/audit_report.md       human-readable report

Usage
-----
    python scripts/audit_dataset.py
    python scripts/audit_dataset.py --input data/raw/tmdb_movies.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "raw" / "tmdb_movies.jsonl"
DEFAULT_OUTDIR = REPO_ROOT / "data" / "audit"

# --- Definitions used for the "usable for content-based" figure. These mirror
# --- the candidate cleaning rules in the phase-02 doc, but are applied ONLY as
# --- counters here, never as deletions.
MIN_OVERVIEW_CHARS = 40     # below this an "overview" is a stub, not prose
MIN_KEYWORDS = 1
MIN_CAST = 1
HAS_TITLE = True
HAS_RELEASE_DATE = True

# Text that indicates a placeholder rather than a real synopsis.
PLACEHOLDER_PATTERNS = [
    r"^no overview available\.?$",
    r"^no overview found\.?$",
    r"^n/?a$",
    r"^unknown$",
    r"^tbd$",
    r"^coming soon$",
    r"^lorem ipsum",
    r"^\W*$",
]
PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_PATTERNS), re.IGNORECASE)

# Titles that suggest test/junk rows in a scraped dataset.
JUNK_TITLE_RE = re.compile(
    r"^(test|untitled|untitled\d*|dummy|placeholder|sample|example|movie|film|"
    r"asdf|qwerty|xxx+|\?+)$",
    re.IGNORECASE,
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_blank(v: Any) -> bool:
    """A value counts as missing if it is None, an empty/whitespace string, or an empty list."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    return False


def name_list(rows: Any) -> list[str]:
    """Extract non-blank 'name' values from a TMDb list-of-dicts field."""
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if isinstance(r, dict):
            n = r.get("name")
            if isinstance(n, str) and n.strip():
                out.append(n.strip())
        elif isinstance(r, str) and r.strip():
            out.append(r.strip())
    return out


def normalise_title(t: str) -> str:
    """
    Aggressive normalisation for duplicate-title grouping (not for display).

    Unicode-aware on purpose: an ASCII-only character class collapses every
    non-Latin title (CJK, Georgian, Greek, ...) onto the empty string, which
    fabricates large bogus "duplicate" groups and false same-title-same-year
    collisions. Accents are folded, all other word characters are preserved,
    and a non-Latin title that still folds to nothing keeps its own text so it
    can only ever group with an identical title.
    """
    original = t or ""
    folded = unicodedata.normalize("NFKD", original.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"\(\d{4}\)\s*$", "", folded)      # trailing release year
    folded = re.sub(r"[^\w]+", " ", folded, flags=re.UNICODE)
    folded = re.sub(r"\s+", " ", folded).strip()
    if not folded:
        return re.sub(r"\s+", " ", original.lower()).strip()
    return folded


def describe(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    sv = sorted(values)
    return {
        "n": len(sv),
        "min": round(sv[0], 2),
        "p25": round(statistics.quantiles(sv, n=4)[0], 2) if len(sv) > 3 else None,
        "median": round(statistics.median(sv), 2),
        "mean": round(statistics.fmean(sv), 2),
        "p75": round(statistics.quantiles(sv, n=4)[2], 2) if len(sv) > 3 else None,
        "p95": round(statistics.quantiles(sv, n=20)[18], 2) if len(sv) > 19 else None,
        "max": round(sv[-1], 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit a TMDb movie snapshot (read-only).")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--show", type=int, default=10, help="rows to show per sample list")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"ERROR: snapshot not found: {args.input}\nRun scripts/fetch_tmdb_snapshot.py first.")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- load (streaming, and record malformed lines) ----------
    records: list[dict[str, Any]] = []
    malformed_lines: list[dict[str, Any]] = []
    total_lines = 0
    with args.input.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed_lines.append({"line": lineno, "error": str(exc), "excerpt": line[:120]})
                continue
            if not isinstance(obj, dict):
                malformed_lines.append({"line": lineno, "error": "top-level JSON is not an object",
                                       "excerpt": line[:120]})
                continue
            obj["__line__"] = lineno
            records.append(obj)

    n = len(records)
    pct = lambda c: round(100.0 * c / n, 2) if n else 0.0

    # ---------------- id integrity -----------------------------------------
    id_counter: Counter = Counter()
    non_int_ids: list[dict[str, Any]] = []
    for r in records:
        mid = r.get("id")
        if isinstance(mid, bool) or not isinstance(mid, int):
            non_int_ids.append({"line": r["__line__"], "id": repr(mid)})
            id_counter[repr(mid)] += 1
        else:
            id_counter[mid] += 1
    duplicate_ids = {k: v for k, v in id_counter.items() if v > 1 and k != "None"}
    invalid_ids = [dict(line=e["line"], id=e["id"]) for e in non_int_ids] + \
                  [dict(id=k, count=v) for k, v in duplicate_ids.items()]

    # ---------------- field presence ---------------------------------------
    missing = Counter()
    for r in records:
        if is_blank(r.get("title")):
            missing["title"] += 1
        if is_blank(r.get("overview")):
            missing["overview_blank"] += 1
        if is_blank(name_list(r.get("genres"))):
            missing["genres"] += 1
        if is_blank(name_list(r.get("keywords"))):
            missing["keywords"] += 1
        if is_blank(name_list(r.get("cast"))):
            missing["cast"] += 1
        rd = r.get("release_date")
        if is_blank(rd):
            missing["release_date_blank"] += 1
        elif not (isinstance(rd, str) and DATE_RE.match(rd.strip())):
            missing["release_date_malformed"] += 1

    # director: derived from retained crew jobs
    directors_by_record: list[list[str]] = []
    for r in records:
        d = [c.get("name", "").strip() for c in (r.get("crew") or [])
             if isinstance(c, dict) and c.get("job") == "Director" and isinstance(c.get("name"), str)
             and c.get("name").strip()]
        directors_by_record.append(d)
    missing["director"] = sum(1 for d in directors_by_record if not d)

    # overview quality: blank vs present-but-placeholder vs present-but-stub
    ov_len_chars: list[int] = []
    ov_len_words: list[int] = []
    placeholder_overviews: list[dict[str, Any]] = []
    stub_overviews: list[dict[str, Any]] = []
    for r in records:
        ov = r.get("overview")
        if is_blank(ov):
            continue
        ov = str(ov).strip()
        if PLACEHOLDER_RE.match(ov):
            placeholder_overviews.append({"line": r["__line__"], "id": r.get("id"),
                                          "title": r.get("title"), "overview": ov[:80]})
            continue
        ov_len_chars.append(len(ov))
        ov_len_words.append(len(ov.split()))
        if len(ov) < MIN_OVERVIEW_CHARS:
            stub_overviews.append({"line": r["__line__"], "id": r.get("id"),
                                   "title": r.get("title"), "chars": len(ov), "overview": ov[:80]})

    # ---------------- duplicates -------------------------------------------
    exact_title_groups: dict[str, list[int]] = defaultdict(list)
    norm_title_groups: dict[str, list[int]] = defaultdict(list)
    for r in records:
        t = r.get("title")
        if isinstance(t, str) and t.strip():
            exact_title_groups[t.strip()].append(r.get("id"))
            norm_title_groups[normalise_title(t)].append(r.get("id"))
    dup_exact_titles = {k: v for k, v in exact_title_groups.items() if len(v) > 1}
    dup_norm_titles = {k: v for k, v in norm_title_groups.items() if len(v) > 1}
    # same normalised title AND same release year = likely true duplicate
    dup_title_year = 0
    title_year: dict[tuple[str, str], set] = defaultdict(set)
    for r in records:
        t, rd = r.get("title"), r.get("release_date")
        if isinstance(t, str) and t.strip() and isinstance(rd, str) and DATE_RE.match(rd.strip()):
            title_year[(normalise_title(t), rd.strip()[:4])].add(r.get("id"))
    for k, ids in title_year.items():
        if len(ids) > 1:
            dup_title_year += len(ids) - 1

    # ---------------- junk detection ---------------------------------------
    junk_titles = [{"line": r["__line__"], "id": r.get("id"), "title": r.get("title")}
                   for r in records
                   if isinstance(r.get("title"), str) and JUNK_TITLE_RE.match(r["title"].strip())]
    numeric_only_titles = sum(
        1 for r in records
        if isinstance(r.get("title"), str) and re.fullmatch(r"[\d\s.\-]+", r["title"].strip() or "x")
    )

    # ---------------- collections / field structure ------------------------
    keyword_counts: list[int] = []
    cast_counts: list[int] = []
    genre_counts: list[int] = []
    runtime_vals: list[float] = []
    crew_job_counter: Counter = Counter()
    for r, dirs in zip(records, directors_by_record):
        kws, cast, gnr = name_list(r.get("keywords")), name_list(r.get("cast")), name_list(r.get("genres"))
        if kws:
            keyword_counts.append(len(kws))
        if cast:
            cast_counts.append(len(cast))
        if gnr:
            genre_counts.append(len(gnr))
        rt = r.get("runtime")
        if isinstance(rt, (int, float)) and not isinstance(rt, bool) and rt > 0:
            runtime_vals.append(float(rt))
        for c in (r.get("crew") or []):
            if isinstance(c, dict) and c.get("job"):
                crew_job_counter[c["job"]] += 1

    # cast ordering predictability: is 'order' present, sequential, and is the
    # stored order ascending? (TMDb guarantees billing order; verify, don't assume.)
    cast_order_present = cast_order_monotonic = cast_sorted_by_order = 0
    for r in records:
        cast = r.get("cast")
        if not isinstance(cast, list) or not cast:
            continue
        orders = [c.get("order") for c in cast if isinstance(c, dict)]
        if orders and all(isinstance(o, int) for o in orders):
            cast_order_present += 1
            if orders == sorted(orders):
                cast_order_monotonic += 1
            if [c.get("name") for c in cast] == [c.get("name") for c in sorted(
                    (c for c in cast if isinstance(c, dict)), key=lambda c: c.get("order", 1 << 30))]:
                cast_sorted_by_order += 1

    multi_director = sum(1 for d in directors_by_record if len(d) > 1)

    # ---------------- distributions ----------------------------------------
    genre_freq: Counter = Counter()
    for r in records:
        for g in name_list(r.get("genres")):
            genre_freq[g] += 1
    keyword_freq: Counter = Counter()
    for r in records:
        for k in name_list(r.get("keywords")):
            keyword_freq[k] += 1
    year_freq: Counter = Counter()
    for r in records:
        rd = r.get("release_date")
        if isinstance(rd, str) and DATE_RE.match(rd.strip()):
            year_freq[rd.strip()[:4]] += 1
    lang_freq: Counter = Counter()
    for r in records:
        if isinstance(r.get("original_language"), str):
            lang_freq[r["original_language"]] += 1
    adult_count = sum(1 for r in records if r.get("adult") is True)
    collection_count = sum(
        1 for r in records if isinstance(r.get("belongs_to_collection"), dict)
    )

    # ---------------- usability (counted, NOT applied) ----------------------
    def _has_overview_prose(r: dict[str, Any]) -> bool:
        ov = r.get("overview")
        return (not is_blank(ov)
                and not PLACEHOLDER_RE.match(str(ov).strip())
                and len(str(ov).strip()) >= MIN_OVERVIEW_CHARS)

    def _has_valid_date(r: dict[str, Any]) -> bool:
        rd = r.get("release_date")
        return isinstance(rd, str) and bool(DATE_RE.match(rd.strip()))

    counts = dict.fromkeys(("has_valid_id", "has_title", "has_release_date", "has_overview_prose", "has_genres", "has_keywords", "has_cast", "has_director"), 0)
    for i, r in enumerate(records):
        if isinstance(r.get("id"), int) and not isinstance(r.get("id"), bool):
            counts["has_valid_id"] += 1
        if not is_blank(r.get("title")):
            counts["has_title"] += 1
        if _has_valid_date(r):
            counts["has_release_date"] += 1
        if _has_overview_prose(r):
            counts["has_overview_prose"] += 1
        if name_list(r.get("genres")):
            counts["has_genres"] += 1
        if name_list(r.get("keywords")):
            counts["has_keywords"] += 1
        if name_list(r.get("cast")):
            counts["has_cast"] += 1
        if directors_by_record[i]:
            counts["has_director"] += 1

    # Tiered usability, mirroring the tiered cleaning rules in the doc.
    tier_full = sum(
        1 for i, r in enumerate(records)
        if isinstance(r.get("id"), int) and not isinstance(r.get("id"), bool)
        and not is_blank(r.get("title"))
        and _has_valid_date(r)
        and _has_overview_prose(r)
        and name_list(r.get("genres")) and name_list(r.get("keywords"))
        and name_list(r.get("cast")) and directors_by_record[i]
    )
    tier_usable = sum(
        1 for r in records
        if isinstance(r.get("id"), int) and not isinstance(r.get("id"), bool)
        and not is_blank(r.get("title"))
        and _has_valid_date(r)
        and name_list(r.get("genres")) and name_list(r.get("cast"))
    )
    tier_minimal = sum(
        1 for r in records
        if isinstance(r.get("id"), int) and not isinstance(r.get("id"), bool)
        and not is_blank(r.get("title"))
    )

    # ---------------- assemble ---------------------------------------------
    size_bytes = args.input.stat().st_size
    h = hashlib.sha256()
    with args.input.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)

    S = args.show
    report: dict[str, Any] = {
        "audit_metadata": {
            "input_file": str(args.input.name),
            "input_size_bytes": size_bytes,
            "input_size_mib": round(size_bytes / 1_048_576, 2),
            "input_sha256": h.hexdigest(),
            "audited_utc": datetime.now(UTC).isoformat(),
            "note": "read-only audit; no records modified, dropped or deduplicated",
        },
        "totals": {
            "total_lines_read": total_lines,
            "malformed_lines": len(malformed_lines),
            "records_parsed": n,
            "unique_ids": len(id_counter),
            "duplicate_id_rows": sum(v - 1 for v in duplicate_ids.values()),
            "invalid_id_rows": len(invalid_ids),
        },
        "missing_data": {
            "counts": dict(missing),
            "percent": {k: pct(v) for k, v in missing.items()},
        },
        "overview_quality": {
            "blank": missing["overview_blank"],
            "placeholder_text": len(placeholder_overviews),
            "stub_under_min_chars": len(stub_overviews),
            "prose_over_min_chars": counts["has_overview_prose"],
            "char_length": describe([float(x) for x in ov_len_chars]),
            "word_length": describe([float(x) for x in ov_len_words]),
            "min_overview_chars_threshold": MIN_OVERVIEW_CHARS,
            "placeholder_samples": placeholder_overviews[:S],
            "stub_samples": stub_overviews[:S],
        },
        "duplicates": {
            "duplicate_exact_title_groups": len(dup_exact_titles),
            "rows_in_exact_title_groups": sum(len(v) for v in dup_exact_titles.values()),
            "duplicate_normalised_title_groups": len(dup_norm_titles),
            "rows_in_normalised_title_groups": sum(len(v) for v in dup_norm_titles.values()),
            "redundant_rows_same_normalised_title_and_year": dup_title_year,
            "samples": [
                {"normalised_title": k, "ids": v[:8], "count": len(v)}
                for k, v in sorted(dup_norm_titles.items(), key=lambda kv: -len(kv[1]))[:S]
            ],
        },
        "junk_records": {
            "junk_title_rows": len(junk_titles),
            "junk_title_samples": junk_titles[:S],
            "numeric_only_titles": numeric_only_titles,
        },
        "field_structure": {
            "genres_per_movie": describe([float(x) for x in genre_counts]),
            "keywords_per_movie": describe([float(x) for x in keyword_counts]),
            "cast_per_movie": describe([float(x) for x in cast_counts]),
            "runtime_minutes": describe(runtime_vals),
            "crew_job_counts": dict(crew_job_counter),
            "multi_director_records": multi_director,
            "cast_order_present": cast_order_present,
            "cast_order_ascending": cast_order_monotonic,
            "cast_already_sorted_by_order": cast_sorted_by_order,
        },
        "distributions": {
            "genres": dict(genre_freq.most_common()),
            "distinct_genres": len(genre_freq),
            "keywords_top": dict(keyword_freq.most_common(40)),
            "distinct_keywords": len(keyword_freq),
            "keywords_singleton": sum(1 for v in keyword_freq.values() if v == 1),
            "years": dict(sorted(year_freq.items())),
            "original_languages_top": dict(lang_freq.most_common(15)),
            "adult_records": adult_count,
            "collection_records": collection_count,
        },
        "usability": {
            "field_level_counts": counts,
            "field_level_percent": {k: pct(v) for k, v in counts.items()},
            "tier_full_all_fields": {"count": tier_full, "percent": pct(tier_full)},
            "tier_usable_core": {"count": tier_usable, "percent": pct(tier_usable),
                                 "requires": ["valid id", "title", "release_date", "genres", "cast"]},
            "tier_minimal": {"count": tier_minimal, "percent": pct(tier_minimal),
                             "requires": ["valid id", "title"]},
        },
        "malformed_detail": {
            "malformed_lines": malformed_lines[:S],
            "invalid_id_samples": invalid_ids[:S],
        },
    }

    (args.out_dir / "audit_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---------------- markdown report --------------------------------------
    md: list[str] = []
    A = md.append
    A("# MovieMind — TMDb snapshot audit (auto-generated)\n")
    A(f"- Input: `{report['audit_metadata']['input_file']}`  ")
    A(f"- Size: {size_bytes:,} bytes ({report['audit_metadata']['input_size_mib']} MiB)  ")
    A(f"- SHA-256: `{h.hexdigest()}`  ")
    A(f"- Audited (UTC): {report['audit_metadata']['audited_utc']}  ")
    A(f"- {report['audit_metadata']['note']}\n")

    A("## Totals\n")
    t = report["totals"]
    A("| Metric | Value |\n|---|---:|")
    for k, v in t.items():
        A(f"| {k.replace('_',' ')} | {v:,} |")

    A("\n## Missing data\n")
    A("| Field | Missing | % of records |\n|---|---:|---:|")
    for k, v in sorted(missing.items(), key=lambda kv: -kv[1]):
        A(f"| {k} | {v:,} | {pct(v)}% |")

    A("\n## Overview text quality\n")
    oq = report["overview_quality"]
    A(f"- blank: {oq['blank']:,}\n- placeholder text: {oq['placeholder_text']:,}\n"
      f"- stub (<{oq['min_overview_chars_threshold']} chars): {oq['stub_under_min_chars']:,}\n"
      f"- usable prose: {oq['prose_over_min_chars']:,} ({pct(oq['prose_over_min_chars'])}%)\n")
    A(f"- char length: {oq['char_length']}\n- word length: {oq['word_length']}\n")

    A("\n## Field structure\n")
    fs = report["field_structure"]
    for k in ("genres_per_movie", "keywords_per_movie", "cast_per_movie", "runtime_minutes"):
        A(f"- {k.replace('_',' ')}: {fs[k]}")
    A(f"- multi-director records: {fs['multi_director_records']:,}")
    A(f"- cast has integer 'order': {fs['cast_order_present']:,}")
    A(f"- cast order ascending: {fs['cast_order_ascending']:,}")
    A(f"- cast already sorted by order: {fs['cast_already_sorted_by_order']:,}")
    A(f"- crew job counts: {fs['crew_job_counts']}\n")

    A("\n## Duplicates\n")
    d = report["duplicates"]
    A(f"- duplicate exact-title groups: {d['duplicate_exact_title_groups']:,}")
    A(f"- duplicate normalised-title groups: {d['duplicate_normalised_title_groups']:,}")
    A(f"- redundant rows (same normalised title + year): {d['redundant_rows_same_normalised_title_and_year']:,}\n")

    A("\n## Distributions\n")
    dist = report["distributions"]
    A(f"- distinct genres: {dist['distinct_genres']}")
    A(f"- distinct keywords: {dist['distinct_keywords']:,} "
      f"(singleton keywords: {dist['keywords_singleton']:,})")
    A(f"- adult records: {dist['adult_records']:,}")
    A(f"- collection records: {dist['collection_records']:,}")
    A(f"- original languages (top 15): {dist['original_languages_top']}\n")
    A("Top genres:\n")
    A("| Genre | Films |\n|---|---:|")
    for g, c in dist["genres"].items():
        A(f"| {g} | {c:,} |")
    A("\nTop 40 keywords:\n")
    A("| Keyword | Films |\n|---|---:|")
    for k, c in dist["keywords_top"].items():
        A(f"| {k} | {c:,} |")

    A("\n## Usability\n")
    u = report["usability"]
    A("| Condition | Records | % |\n|---|---:|---:|")
    for k, v in u["field_level_counts"].items():
        A(f"| {k} | {v:,} | {u['field_level_percent'][k]}% |")
    A("")
    for tier in ("tier_full_all_fields", "tier_usable_core", "tier_minimal"):
        A(f"- **{tier}**: {u[tier]['count']:,} ({u[tier]['percent']}%)")
    A("\n## Malformed / invalid\n")
    A(f"- malformed lines: {len(malformed_lines):,}")
    A(f"- invalid id rows: {len(invalid_ids):,}")
    if malformed_lines:
        A("\nMalformed line samples:\n")
        A("```")
        for m in malformed_lines[:S]:
            A(f"line {m['line']}: {m['error']} | {m['excerpt']}")
        A("```")

    (args.out_dir / "audit_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---------------- console summary --------------------------------------
    print(f"audited            : {n:,} records from {args.input.name}")
    print(f"snapshot size      : {size_bytes:,} bytes ({size_bytes / 1_048_576:.2f} MiB)")
    print(f"malformed lines    : {len(malformed_lines)}")
    print(f"duplicate id rows  : {report['totals']['duplicate_id_rows']}")
    print(f"invalid id rows    : {len(invalid_ids)}")
    print("--- missing / low-quality ---")
    for k, v in sorted(missing.items(), key=lambda kv: -kv[1]):
        print(f"  {k:26s} {v:6,}  {pct(v):6.2f}%")
    print(f"  {'overview_placeholder':26s} {len(placeholder_overviews):6,}  "
          f"{pct(len(placeholder_overviews)):6.2f}%")
    print(f"  {'overview_stub':26s} {len(stub_overviews):6,}  {pct(len(stub_overviews)):6.2f}%")
    print("--- duplicates ---")
    print(f"  duplicate exact-title groups   {report['duplicates']['duplicate_exact_title_groups']:,}")
    print(f"  duplicate normalised groups    {report['duplicates']['duplicate_normalised_title_groups']:,}")
    print(f"  redundant rows (title+year)    {dup_title_year:,}")
    print(f"  junk title rows                {report['junk_records']['junk_title_rows']:,}")
    print("--- usability ---")
    for k, v in u["field_level_counts"].items():
        print(f"  {k:26s} {v:6,}  {u['field_level_percent'][k]:6.2f}%")
    print(f"  TIER full (all fields)         {tier_full:6,}  {pct(tier_full):6.2f}%")
    print(f"  TIER usable (core)             {tier_usable:6,}  {pct(tier_usable):6.2f}%")
    print(f"  TIER minimal (id+title)        {tier_minimal:6,}  {pct(tier_minimal):6.2f}%")
    print("--- distributions ---")
    print(f"  distinct genres   : {len(genre_freq)}")
    print(f"  distinct keywords : {len(keyword_freq):,} (singletons {sum(1 for v in keyword_freq.values() if v == 1):,})")
    print(f"  top genres        : {dict(genre_freq.most_common(8))}")
    print(f"  adult records     : {adult_count:,}")
    print(f"\nwrote {args.out_dir / 'audit_summary.json'}")
    print(f"wrote {args.out_dir / 'audit_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
