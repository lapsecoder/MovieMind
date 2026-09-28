"""
Shared fixtures. Tests never touch the real snapshot except in the two
integration tests that explicitly opt in, so the suite runs without the
(non-committable) raw data present.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moviemind.config import PreprocessConfig  # noqa: E402

RAW_SNAPSHOT = REPO_ROOT / "data" / "raw" / "tmdb_movies.jsonl"
PROCESSED_CORPUS = REPO_ROOT / "data" / "processed" / "movies.jsonl"


@pytest.fixture
def config() -> PreprocessConfig:
    return PreprocessConfig()


@pytest.fixture
def raw_available() -> bool:
    return RAW_SNAPSHOT.exists()


@pytest.fixture
def processed_available() -> bool:
    return PROCESSED_CORPUS.exists()


def make_record(
    movie_id: int = 1,
    title: str = "Test Film",
    overview: str | None = "A determined detective confronts a sprawling conspiracy across three cities.",
    genres: list[str] | None = None,
    keywords: list[str] | None = None,
    cast: list[str] | None = None,
    directors: list[str] | None = None,
    **extra: object,
) -> dict:
    """
    Build a synthetic raw record shaped like the real projection.

    List arguments use ``None`` as "absent" and distinguish it from an explicit
    empty list, so a test can assert that a genuinely empty field produces an
    empty block.
    """
    record: dict = {
        "id": movie_id,
        "title": title,
        "original_title": title,
        "overview": overview,
        "genres": [
            {"id": i, "name": g}
            for i, g in enumerate(["Drama"] if genres is None else genres)
        ],
        "keywords": [
            {"id": i, "name": k}
            for i, k in enumerate(["detective"] if keywords is None else keywords)
        ],
        "cast": [
            {"id": i, "name": n, "character": f"Role {i}", "order": i}
            for i, n in enumerate(
                ["Ada Lovelace", "Grace Hopper"] if cast is None else cast
            )
        ],
        "crew": [
            {"id": i, "name": n, "job": "Director", "department": "Directing"}
            for i, n in enumerate(["Kenji Watanabe"] if directors is None else directors)
        ],
        "release_date": "1999-05-07",
        "runtime": 120,
        "original_language": "en",
        "status": "Released",
        "popularity": 12.5,
        "vote_average": 7.4,
        "vote_count": 340,
        "adult": False,
        "belongs_to_collection": None,
    }
    record.update(extra)
    return record


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
