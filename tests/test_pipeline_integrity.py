"""
Loading guarantees: duplicate IDs, malformed input, determinism, and the
read-only contract on `data/raw/`.
"""

from __future__ import annotations

import json

from conftest import make_record, write_jsonl

from moviemind.pipeline import (
    LoadReport,
    _coerce_id,
    build_corpus,
    build_movie_features,
    iter_raw_records,
    sha256_file,
    write_corpus,
)


# ---------------------------------------------------------------------------
# id handling
# ---------------------------------------------------------------------------
def test_numeric_string_ids_are_accepted():
    assert _coerce_id(42) == 42
    assert _coerce_id("42") == 42
    assert _coerce_id(" 42 ") == 42


def test_unusable_ids_are_rejected():
    for value in (None, "", "abc", "12.5", [], {}, True, False, 3.5, ""):
        assert _coerce_id(value) is None, value


def test_record_without_usable_id_is_not_built(config):
    record = make_record()
    record["id"] = "not-an-id"
    assert build_movie_features(record, config) is None


def test_duplicate_id_keeps_first_occurrence(tmp_path, config):
    raw = tmp_path / "raw.jsonl"
    write_jsonl(
        raw,
        [
            make_record(movie_id=1, title="First", overview="The first film arrives here with text."),
            make_record(movie_id=1, title="Second", overview="The second film is a different one."),
        ],
    )
    features, report = build_corpus(raw, config)
    assert len(features) == 1
    assert features[0]["title"] == "First"
    assert report.duplicate_id_records == 1
    assert report.records_parsed == 1


def test_duplicate_rows_are_reported_not_silently_dropped(tmp_path, config):
    raw = tmp_path / "raw.jsonl"
    write_jsonl(raw, [make_record(movie_id=7), make_record(movie_id=7), make_record(movie_id=7)])
    features, report = build_corpus(raw, config)
    assert len(features) == 1
    assert report.duplicate_id_records == 2
    assert report.duplicate_id_samples[0]["kept"] == "first occurrence"


# ---------------------------------------------------------------------------
# remakes and re-releases share titles but not IDs
# ---------------------------------------------------------------------------
def test_same_title_different_ids_are_both_kept(tmp_path, config):
    """The Phase 2 audit found 69 same-title groups; all are legitimate
    remakes or re-releases, so title must never be a key."""
    raw = tmp_path / "raw.jsonl"
    write_jsonl(
        raw,
        [
            make_record(movie_id=101, title="The Thing", overview="An antarctic base film of horror."),
            make_record(movie_id=202, title="The Thing", overview="A shape shifting alien horror film."),
        ],
    )
    features, report = build_corpus(raw, config)
    assert len(features) == 2
    assert [f["id"] for f in features] == [101, 202]
    assert report.duplicate_id_records == 0


# ---------------------------------------------------------------------------
# malformed input
# ---------------------------------------------------------------------------
def test_malformed_line_is_skipped_not_fatal(tmp_path, config):
    raw = tmp_path / "raw.jsonl"
    write_jsonl(raw, [make_record(movie_id=1), make_record(movie_id=2)])
    with raw.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
        handle.write("[1, 2, 3]\n")
        handle.write("null\n")
    features, report = build_corpus(raw, config)
    assert len(features) == 2
    assert report.malformed_lines == 3
    assert report.records_parsed == 2


def test_blank_lines_are_not_counted_as_malformed(tmp_path, config):
    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        json.dumps(make_record(movie_id=1)) + "\n\n\n", encoding="utf-8"
    )
    features, report = build_corpus(raw, config)
    assert report.malformed_lines == 0
    assert report.lines_read == 1
    assert len(features) == 1


def test_records_with_invalid_id_are_counted_separately(tmp_path, config):
    raw = tmp_path / "raw.jsonl"
    bad = make_record(movie_id=5)
    bad["id"] = None
    write_jsonl(raw, [make_record(movie_id=1), bad, make_record(movie_id=2)])
    features, report = build_corpus(raw, config)
    assert report.invalid_id_records == 1
    assert report.records_parsed == 2
    assert len(features) == 2


def test_iter_raw_records_reports_error_reason(tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text('{"id": 1}\n{oops\n', encoding="utf-8")
    results = list(iter_raw_records(raw))
    assert results[0][1] is not None
    assert results[0][2] is None
    assert results[1][1] is None
    assert "json_decode_error" in results[1][2]


def test_empty_file_produces_empty_corpus(tmp_path, config):
    raw = tmp_path / "raw.jsonl"
    raw.write_text("", encoding="utf-8")
    features, report = build_corpus(raw, config)
    assert features == []
    assert report.records_parsed == 0


# ---------------------------------------------------------------------------
# determinism and immutability
# ---------------------------------------------------------------------------
def _sample_records() -> list[dict]:
    return [
        make_record(
            movie_id=1,
            title="Am\u00e9lie",
            overview="A shy waitress decides to change her life and help others in Paris.",
            keywords=["romance", "based on novel or book"],
            genres=["Romance", "Comedy"],
            cast=["Audrey Tautou", "Mathieu Kassovitz"],
            directors=["Jean-Pierre Jeunet"],
        ),
        make_record(
            movie_id=2,
            title="\u677f\u5c71\u4e4b\u6b87",
            overview="\u5f79\u8005\u306f\u8b64\u306b\u8abf\u3079\u3089\u308c\u308b",
            keywords=["1970s"],
            genres=["Drama"],
            cast=[],
            directors=[],
        ),
        make_record(movie_id=3, overview=None, keywords=[], genres=[], cast=[], directors=[]),
    ]


def test_two_builds_are_byte_identical(tmp_path, config):
    raw = write_jsonl(tmp_path / "raw.jsonl", _sample_records())
    a = write_corpus(build_corpus(raw, config)[0], tmp_path / "a", config,
                     report=LoadReport(), raw_path=raw)
    b = write_corpus(build_corpus(raw, config)[0], tmp_path / "b", config,
                     report=LoadReport(), raw_path=raw)
    assert a["corpus"]["sha256"] == b["corpus"]["sha256"]


def test_corpus_file_carries_no_timestamp(tmp_path, config):
    """Timestamps belong in the manifest, or the corpus could never be
    diffed or hash-compared between runs."""
    raw = write_jsonl(tmp_path / "raw.jsonl", _sample_records())
    write_corpus(build_corpus(raw, config)[0], tmp_path / "out", config,
                 report=LoadReport(), raw_path=raw)
    corpus_text = (tmp_path / "out" / "movies.jsonl").read_text(encoding="utf-8")
    for banned in ("generated_utc", "20" + "26-", "T00:00"):
        assert banned not in corpus_text
    manifest = json.loads((tmp_path / "out" / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["generated_utc"]


def test_corpus_json_keys_are_sorted(tmp_path, config):
    raw = write_jsonl(tmp_path / "raw.jsonl", _sample_records())
    write_corpus(build_corpus(raw, config)[0], tmp_path / "out", config,
                 report=LoadReport(), raw_path=raw)
    first = (tmp_path / "out" / "movies.jsonl").read_text(encoding="utf-8").splitlines()[0]
    keys = list(json.loads(first).keys())
    assert keys == sorted(keys)


def test_raw_input_is_never_modified(tmp_path, config):
    raw = write_jsonl(tmp_path / "raw.jsonl", _sample_records())
    before = sha256_file(raw)
    write_corpus(build_corpus(raw, config)[0], tmp_path / "out", config,
                 report=LoadReport(), raw_path=raw)
    assert sha256_file(raw) == before


def test_source_record_is_not_mutated(config):
    record = make_record()
    before = json.dumps(record, sort_keys=True, ensure_ascii=False)
    build_movie_features(record, config)
    assert json.dumps(record, sort_keys=True, ensure_ascii=False) == before


def test_unicode_is_written_literally_not_escaped(tmp_path, config):
    raw = write_jsonl(tmp_path / "raw.jsonl", _sample_records())
    write_corpus(build_corpus(raw, config)[0], tmp_path / "out", config,
                 report=LoadReport(), raw_path=raw)
    text = (tmp_path / "out" / "movies.jsonl").read_text(encoding="utf-8")
    assert "\u677f\u5c71\u4e4b\u6b87" in text
    assert "\\u677f" not in text


# ---------------------------------------------------------------------------
# config fingerprint
# ---------------------------------------------------------------------------
def test_fingerprint_is_stable_and_sensitive(config):
    import dataclasses

    assert config.fingerprint() == config.fingerprint()
    changed = dataclasses.replace(config, block_mode="flat")
    assert changed.fingerprint() != config.fingerprint()
    other = dataclasses.replace(config, cast=dataclasses.replace(config.cast, top_k=99))
    assert other.fingerprint() != config.fingerprint()
    no_stops = dataclasses.replace(
        config, overview=dataclasses.replace(config.overview, use_english_stopwords=False)
    )
    assert no_stops.fingerprint() != config.fingerprint()
