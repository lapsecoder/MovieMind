"""
Invariants checked against the real 5,000-film snapshot.

Skipped automatically when the raw data is absent, so the suite still runs on a
fresh clone. These assert properties that must hold for *any* config, not the
tuned numbers, so they keep working when the config is deliberately changed.
"""

from __future__ import annotations

import json

import pytest
from conftest import PROCESSED_CORPUS, RAW_SNAPSHOT

from moviemind.config import CREDITS_STINGER_KEYWORDS
from moviemind.representations import REPRESENTATIONS, build_representation

pytestmark = pytest.mark.skipif(
    not (RAW_SNAPSHOT.exists() and PROCESSED_CORPUS.exists()),
    reason="requires the real snapshot and a completed build",
)

EXPECTED_RAW_SHA256 = "5b1079cd46efad0dec21f74736bebc6db6ba2f91e1b7497365c86225fd36307b"


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    with PROCESSED_CORPUS.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_raw_snapshot_is_unchanged():
    from moviemind.pipeline import sha256_file

    assert sha256_file(RAW_SNAPSHOT) == EXPECTED_RAW_SHA256


def test_every_film_has_a_unique_numeric_id(corpus):
    ids = [item["id"] for item in corpus]
    assert len(ids) == len(set(ids)) == 5000
    assert all(isinstance(i, int) and i > 0 for i in ids)


def test_no_stinger_keyword_survives(corpus):
    for item in corpus:
        squeezed = {t.replace("_", "").removeprefix("kw:").replace(" ", "") for t in item["tokens"]["keywords"]}
        assert not squeezed & CREDITS_STINGER_KEYWORDS


def test_stinger_noise_was_actually_found(corpus):
    """A silent regression that stopped removing stingers must not pass."""
    assert any(item["stinger_keywords_removed"] for item in corpus)


def test_every_token_is_namespaced(corpus):
    for item in corpus:
        for block, tokens in item["tokens"].items():
            for token in tokens:
                assert ":" in token, token
                assert " " not in token, token


def test_genre_block_never_exceeds_the_controlled_vocabulary(corpus):
    genres = {t for item in corpus for t in item["tokens"]["genres"]}
    assert len(genres) == 19
    assert all(t.startswith("gn:") for t in genres)


def test_documentation_token_share_matches_the_manifest(corpus):
    """Guards against a silent change to what each field contributes."""
    from moviemind.config import PreprocessConfig
    from moviemind.pipeline import build_corpus

    config = PreprocessConfig()
    rebuilt, _ = build_corpus(RAW_SNAPSHOT, config)
    totals: dict[str, int] = {}
    for item in rebuilt:
        for block, tokens in item["tokens"].items():
            totals[block] = totals.get(block, 0) + len(tokens)
    grand = sum(totals.values())
    shares = {k: v / grand for k, v in totals.items()}
    # The cast top_k=10 decision exists to hold cast below a fifth of tokens.
    assert shares["cast"] < 0.20, shares
    assert shares["overview"] > 0.40, shares
    # Genres are a controlled 19-label field; they must not grow.
    assert shares["genres"] < 0.06, shares


def test_representation_d_covers_every_film(corpus):
    from moviemind.config import PreprocessConfig

    result = build_representation(corpus, REPRESENTATIONS[3], PreprocessConfig())
    assert result.all_zero_documents == 0
    assert result.n_documents == 5000


def test_representation_a_leaves_exactly_the_films_without_an_overview_empty(corpus):
    from moviemind.config import PreprocessConfig

    result = build_representation(corpus, REPRESENTATIONS[0], PreprocessConfig())
    without_overview = sum(1 for item in corpus if not item["tokens"]["overview"])
    assert result.all_zero_documents == without_overview


def test_unicode_titles_survive_the_pipeline(corpus):
    assert any(any(ord(c) > 127 for c in (item["title"] or "")) for item in corpus)
    for item in corpus:
        assert item["title"] is None or isinstance(item["title"], str)


def test_manifest_records_the_raw_hash_and_config_fingerprint():
    manifest = json.loads(
        (PROCESSED_CORPUS.parent / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["raw_sha256"] == EXPECTED_RAW_SHA256
    assert manifest["load_report"]["malformed_lines"] == 0
    assert manifest["load_report"]["invalid_id_records"] == 0
    assert manifest["load_report"]["duplicate_id_records"] == 0
    assert manifest["licence"]["raw_data_must_not_be_committed"] is True
    assert len(manifest["config_fingerprint"]) == 64
