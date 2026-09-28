"""
Raw snapshot -> deterministic per-movie feature documents.

Read-only with respect to `data/raw/`. This module never writes to the input
path, never mutates the parsed record, and never deduplicates by title:
TMDb `id` is the sole canonical identifier.

The Phase 2 audit established that the 5,000-film snapshot contains 0 true
duplicates and 69 legitimate same-title groups (remakes and re-releases), so
title-based deduplication would delete real films. If duplicate IDs are ever
encountered, the first occurrence wins and the rest are counted and reported
rather than silently merged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import (
    CREDITS_STINGER_KEYWORDS,
    PreprocessConfig,
)
from .text import (
    canonical_phrase,
    dedupe_preserving_order,
    phrase_tokens,
    tokenize,
)


# ---------------------------------------------------------------------------
# raw loading
# ---------------------------------------------------------------------------
@dataclass
class LoadReport:
    """What happened while reading the raw file. Recorded in the manifest."""

    lines_read: int = 0
    records_parsed: int = 0
    malformed_lines: int = 0
    non_object_records: int = 0
    invalid_id_records: int = 0
    duplicate_id_records: int = 0
    malformed_samples: list[dict[str, Any]] = field(default_factory=list)
    duplicate_id_samples: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines_read": self.lines_read,
            "records_parsed": self.records_parsed,
            "malformed_lines": self.malformed_lines,
            "non_object_records": self.non_object_records,
            "invalid_id_records": self.invalid_id_records,
            "duplicate_id_records": self.duplicate_id_records,
            "malformed_samples": self.malformed_samples[:10],
            "duplicate_id_samples": self.duplicate_id_samples[:10],
        }


def _coerce_id(value: object) -> int | None:
    """TMDb ids are integers. Anything else is not a usable key."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def iter_raw_records(path: str | Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    """
    Yield ``(line_number, record, error)`` for each non-empty line.

    A malformed line yields ``(n, None, reason)`` rather than raising, so a
    single corrupt line cannot abort a build.
    """
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                yield line_number, None, f"json_decode_error: {exc.msg}"
                continue
            if not isinstance(parsed, dict):
                yield line_number, None, "record_is_not_an_object"
                continue
            yield line_number, parsed, None


# ---------------------------------------------------------------------------
# per-field extraction
# ---------------------------------------------------------------------------
def _as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def extract_overview_tokens(record: dict[str, Any], config: PreprocessConfig) -> list[str]:
    """Free prose. Stub overviews below the length floor contribute nothing."""
    overview = record.get("overview")
    if not isinstance(overview, str):
        overview = "" if overview is None else str(overview)
    if len(overview.strip()) < config.min_overview_chars:
        return []
    return tokenize(
        overview,
        config,
        namespace=config.overview.namespace,
        min_token_length=config.overview.min_token_length,
        drop_function_words=config.overview.use_english_stopwords,
    )


def extract_keyword_tokens(
    record: dict[str, Any],
    config: PreprocessConfig,
    *,
    removed: list[str] | None = None,
) -> list[str]:
    """
    Curated keywords as atomic namespaced phrases.

    Credits-stinger keywords are dropped here rather than in the vectoriser,
    so they are also absent from the processed documents and remain auditable.
    """
    tokens: list[str] = []
    for entry in _as_list(record.get("keywords")):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        phrase = canonical_phrase(name, config)
        if not phrase:
            continue
        # Compare on the squeezed form: the stored values are already
        # run-together, but guarding against "during credits stinger" costs
        # nothing and documents the intent.
        if phrase.replace(" ", "") in CREDITS_STINGER_KEYWORDS:
            if removed is not None:
                removed.append(phrase)
            continue
        tokens.extend(
            phrase_tokens(
                name,
                config,
                namespace=config.keywords.namespace,
                min_token_length=config.keywords.min_token_length,
            )
        )
    return dedupe_preserving_order(tokens)


def extract_genre_tokens(record: dict[str, Any], config: PreprocessConfig) -> list[str]:
    """19 controlled labels, emitted as phrases so 'Science Fiction' stays whole."""
    tokens: list[str] = []
    for entry in _as_list(record.get("genres")):
        if not isinstance(entry, dict):
            continue
        tokens.extend(
            phrase_tokens(
                entry.get("name"),
                config,
                namespace=config.genres.namespace,
                min_token_length=config.genres.min_token_length,
            )
        )
    return dedupe_preserving_order(tokens)


def _name_tokens(
    name: object, config: PreprocessConfig, field_cfg, namespace: str
) -> list[str]:
    """Person names: surname/forename sub-tokens, initials dropped."""
    if not isinstance(name, str):
        return []
    return tokenize(
        name,
        config,
        namespace=namespace,
        min_token_length=field_cfg.min_token_length,
    )


def _sort_key_order(entry: dict[str, Any], position: int) -> tuple[int, int, str]:
    """
    Stable ordering for credits.

    Prefers TMDb's billing `order`, which the audit verified is ascending in
    5,000/5,000 records. Name and position are tie-breakers so the result does
    not depend on input ordering.
    """
    order = entry.get("order")
    order_int = order if isinstance(order, int) and not isinstance(order, bool) else position
    name = str(entry.get("name") or "")
    return (order_int, position, name)


def extract_cast_tokens(record: dict[str, Any], config: PreprocessConfig) -> list[str]:
    """
    Top-billed cast, bounded by ``config.cast.top_k``.

    A cast list is not a bag of equally meaningful names: billing order is a
    real signal and an unbounded list lets a 20-person ensemble outweigh the
    synopsis. See config.CAST_TOP_K_NOTE for the measured token shares.
    """
    entries = [e for e in _as_list(record.get("cast")) if isinstance(e, dict)]
    # Decorate with the original position so the sort is total and stable even
    # if two credit dicts compare equal. `list.index` would be both O(n^2) and
    # ambiguous when duplicates exist.
    positioned = [(pos, e) for pos, e in enumerate(entries)]
    positioned.sort(key=lambda pair: _sort_key_order(pair[1], pair[0]))
    top_k = config.cast.top_k
    if top_k is not None:
        positioned = positioned[:top_k]
    tokens: list[str] = []
    for _, entry in positioned:
        tokens.extend(
            _name_tokens(entry.get("name"), config, config.cast, config.cast.namespace)
        )
    return dedupe_preserving_order(tokens)


def extract_director_tokens(record: dict[str, Any], config: PreprocessConfig) -> list[str]:
    """
    Directors as a *set*, not a scalar.

    285 films in the snapshot are legitimately co-directed (Brave: Mark Andrews
    and Brenda Chapman), so a single-value director field would silently drop
    one director for those films. top_k defaults to 3, above the observed
    maximum, so no co-director is lost in practice.
    """
    entries = [e for e in _as_list(record.get("crew")) if isinstance(e, dict)]
    entries = [e for e in entries if e.get("job") == "Director"]
    entries.sort(key=lambda e: (str(e.get("name") or ""),))
    top_k = config.director.top_k
    if top_k is not None:
        entries = entries[:top_k]
    tokens: list[str] = []
    for entry in entries:
        tokens.extend(
            _name_tokens(entry.get("name"), config, config.director, config.director.namespace)
        )
    return dedupe_preserving_order(tokens)


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------
def build_movie_features(
    record: dict[str, Any], config: PreprocessConfig
) -> dict[str, Any] | None:
    """
    Turn one raw record into a processed feature record.

    Returns ``None`` only when the record has no usable TMDb id, since that is
    the canonical key. A film missing every text field still yields a record
    with empty blocks rather than being dropped.
    """
    tmdb_id = _coerce_id(record.get("id"))
    if tmdb_id is None:
        return None

    removed_noise: list[str] = []
    blocks: dict[str, list[str]] = {
        "overview": extract_overview_tokens(record, config),
        "keywords": extract_keyword_tokens(record, config, removed=removed_noise),
        "genres": extract_genre_tokens(record, config),
        "cast": extract_cast_tokens(record, config),
        "director": extract_director_tokens(record, config),
    }

    # Deterministic document order: fixed field order, tokens already ordered.
    document: list[str] = []
    for name in ("overview", "keywords", "genres", "cast", "director"):
        document.extend(blocks[name])

    runtime = record.get("runtime")
    runtime_minutes = runtime if isinstance(runtime, int) and not isinstance(runtime, bool) else None
    if runtime_minutes is not None and runtime_minutes <= 0:
        # TMDb uses 0 for "not recorded". 80 films affected.
        runtime_minutes = None

    collection = record.get("belongs_to_collection")
    collection_name = None
    if isinstance(collection, dict) and isinstance(collection.get("name"), str):
        candidate = collection["name"].strip()
        if candidate:
            collection_name = candidate

    return {
        "id": tmdb_id,
        "title": record.get("title") if isinstance(record.get("title"), str) else None,
        "original_title": (
            record.get("original_title")
            if isinstance(record.get("original_title"), str)
            else None
        ),
        "release_date": (
            record.get("release_date")
            if isinstance(record.get("release_date"), str)
            else None
        ),
        "release_year": _year_of(record.get("release_date")),
        "original_language": (
            record.get("original_language")
            if isinstance(record.get("original_language"), str)
            else None
        ),
        "runtime_minutes": runtime_minutes,
        "collection_name": collection_name,
        # Display / re-ranking only. Deliberately never tokenised: Phase 1
        # sec 5.2 and Phase 2 sec 12 keep ratings out of the vector.
        "popularity": _number_or_none(record.get("popularity")),
        "vote_average": _number_or_none(record.get("vote_average")),
        "vote_count": _number_or_none(record.get("vote_count")),
        "adult": bool(record.get("adult")) if isinstance(record.get("adult"), bool) else False,
        "tokens": blocks,
        "document": document,
        "document_size": len(document),
        "stinger_keywords_removed": sorted(set(removed_noise)),
    }


def _year_of(value: object) -> int | None:
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def _number_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_corpus(
    raw_path: str | Path, config: PreprocessConfig
) -> tuple[list[dict[str, Any]], LoadReport]:
    """
    Build the processed corpus from the raw snapshot.

    Records are returned in raw file order. Duplicate TMDb ids keep the first
    occurrence; the discarded rows are counted in the report. Title is never
    used as a key.
    """
    report = LoadReport()
    features: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for line_number, record, error in iter_raw_records(raw_path):
        report.lines_read += 1
        if error is not None or record is None:
            report.malformed_lines += 1
            if len(report.malformed_samples) < 10:
                report.malformed_samples.append({"line": line_number, "error": error})
            continue

        raw_id = record.get("id")
        tmdb_id = _coerce_id(raw_id)
        if tmdb_id is None:
            report.invalid_id_records += 1
            if len(report.malformed_samples) < 10:
                report.malformed_samples.append(
                    {"line": line_number, "error": "invalid_id", "value": repr(raw_id)[:80]}
                )
            continue

        if tmdb_id in seen_ids:
            report.duplicate_id_records += 1
            if len(report.duplicate_id_samples) < 10:
                report.duplicate_id_samples.append(
                    {"line": line_number, "id": tmdb_id, "kept": "first occurrence"}
                )
            continue

        built = build_movie_features(record, config)
        if built is None:
            report.invalid_id_records += 1
            continue

        seen_ids.add(tmdb_id)
        features.append(built)
        report.records_parsed += 1

    return features, report


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_corpus(
    features: list[dict[str, Any]],
    out_dir: str | Path,
    config: PreprocessConfig,
    *,
    report: LoadReport,
    raw_path: str | Path,
) -> dict[str, Any]:
    """
    Write the processed corpus and its build manifest.

    Output is byte-for-byte reproducible for a given input and config: keys are
    sorted, separators are fixed, and no timestamp is written into the corpus
    file itself. The timestamp lives only in the manifest.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    corpus_path = out / "movies.jsonl"

    with corpus_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in features:
            handle.write(
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            handle.write("\n")

    token_totals: dict[str, int] = {}
    empty_blocks: dict[str, int] = {}
    nonempty_docs = 0
    stinger_films = 0
    stinger_tokens: set[str] = set()
    for item in features:
        if item["document"]:
            nonempty_docs += 1
        for block, tokens in item["tokens"].items():
            token_totals[block] = token_totals.get(block, 0) + len(tokens)
            if not tokens:
                empty_blocks[block] = empty_blocks.get(block, 0) + 1
        if item["stinger_keywords_removed"]:
            stinger_films += 1
            stinger_tokens.update(item["stinger_keywords_removed"])

    total_tokens = sum(token_totals.values())
    manifest = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "raw_path": str(raw_path),
        "raw_sha256": sha256_file(raw_path),
        "config_fingerprint": config.fingerprint(),
        "config": config.to_dict(),
        "load_report": report.as_dict(),
        "corpus": {
            "path": str(corpus_path),
            "records": len(features),
            "nonempty_documents": nonempty_docs,
            "size_bytes": corpus_path.stat().st_size,
            "sha256": sha256_file(corpus_path),
            "total_tokens": total_tokens,
            "mean_tokens_per_film": round(total_tokens / len(features), 4) if features else 0.0,
            "tokens_by_block": dict(sorted(token_totals.items())),
            "token_share_by_block": {
                k: round(v / total_tokens * 100, 2)
                for k, v in sorted(token_totals.items())
                if total_tokens
            },
            "films_with_empty_block": dict(sorted(empty_blocks.items())),
        },
        "stinger_noise": {
            "films_affected": stinger_films,
            "distinct_phrases_removed": sorted(stinger_tokens),
        },
        "licence": {
            "data_owner": "The Movie Database (TMDb)",
            "usage": "non-commercial retrieval/similarity only; never train on TMDb content",
            "attribution_required": (
                "This [website, program, service, application, product] uses TMDB and the "
                "TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."
            ),
            "raw_data_must_not_be_committed": True,
        },
    }
    manifest_path = out / "build_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
