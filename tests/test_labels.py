"""
Tests for the structural proxy labels.

The labels are the weakest link in the Phase 4 evaluation, so these tests are
mostly about *refusing to be wrong quietly*: correct group boundaries, correct
exclusion of the query, and a hard failure when asked to recover person
identity from data that cannot support it.
"""

from __future__ import annotations

import pytest
from conftest import make_record

from moviemind.labels import (
    MIN_GROUP_SIZE,
    MissingCreditsError,
    ProxyLabels,
    normalise_name,
)


def build(records: list[dict], cast_top_k: int = 5) -> ProxyLabels:
    return ProxyLabels.from_raw(records, cast_top_k=cast_top_k)


# -- normalisation ---------------------------------------------------------
def test_normalise_name_folds_case_and_whitespace():
    assert normalise_name("  Christopher   NOLAN ") == "christopher nolan"


def test_normalise_name_preserves_diacritics():
    """Config sets ``strip_accents=False``: folding would erase the
    distinction between e.g. "Amelie" and "Amélie"."""
    assert normalise_name("Amélie") != normalise_name("Amelie")
    # NFC composes, it does not decompose: the same characters stay equal.
    assert normalise_name("Ångström") == normalise_name("Ångström")


def test_normalise_name_rejects_non_strings():
    assert normalise_name(None) == ""
    assert normalise_name(42) == ""
    assert normalise_name(["a"]) == ""


# -- group construction ----------------------------------------------------
def test_singleton_groups_are_discarded():
    labels = build([make_record(movie_id=1), make_record(movie_id=2, title="Other")])
    assert labels.franchise == {}
    assert labels.same_title == {}


def test_group_of_two_is_kept():
    records = [
        make_record(movie_id=1, title="Shared"),
        make_record(movie_id=2, title="Shared"),
    ]
    labels = build(records)
    assert labels.same_title == {"shared": frozenset({1, 2})}


def test_group_for_excludes_the_query_itself():
    records = [
        make_record(movie_id=1, title="Shared"),
        make_record(movie_id=2, title="Shared"),
    ]
    labels = build(records)
    assert labels.group_for("same_title", 1) == frozenset({2})
    assert labels.group_for("same_title", 2) == frozenset({1})


def test_min_group_size_is_two():
    assert MIN_GROUP_SIZE == 2


def test_group_for_unknown_film_is_empty_not_an_error():
    labels = build([make_record(movie_id=1, title="A"), make_record(movie_id=2, title="A")])
    assert labels.group_for("same_title", 999) == frozenset()
    assert labels.group_for("franchise", 999) == frozenset()


# -- franchise -------------------------------------------------------------
def test_franchise_from_belongs_to_collection():
    records = [
        make_record(movie_id=1, belongs_to_collection={"id": 9, "name": "Dune"}),
        make_record(movie_id=2, belongs_to_collection={"id": 9, "name": "dune "}),
    ]
    labels = build(records)
    assert labels.group_for("franchise", 1) == frozenset({2})


# -- cast ------------------------------------------------------------------
def test_cast_respects_billing_order_before_truncating():
    records = [
        make_record(
            movie_id=1,
            cast=["First", "Second", "Third", "Fourth"],
        ),
        make_record(movie_id=2, cast=["Third", "Fourth"]),
    ]
    # At top_k=2 the query keeps First and Second, which do not overlap the
    # other film's credit, so there is no cast group. Order is what decides
    # this, not alphabetical order or set order.
    assert build(records, cast_top_k=2).cast_films == {}
    # At top_k=4 they share Third and Fourth.
    assert build(records, cast_top_k=4).cast_films != {}


def test_cast_label_cannot_exceed_the_vectors_own_cast_bound():
    """A label built from more credits than the vector saw would credit the
    representation for information it never had."""
    records = [
        make_record(movie_id=1, cast=["Shared One", "Extra A", "Extra B"]),
        make_record(movie_id=2, cast=["Shared One", "Extra C", "Extra D"]),
    ]
    # At top_k=1 both films keep only the shared credit, so they group.
    assert build(records, cast_top_k=1).group_for("cast", 1) == frozenset({2})
    # At top_k=3 the shared credit is still there, so they still group -- the
    # bound removes tail credits, it never removes the ones in common.
    assert build(records, cast_top_k=3).group_for("cast", 1) == frozenset({2})
    # But a disjoint pair never groups, however generous the bound.
    disjoint = [
        make_record(movie_id=1, cast=["Alpha", "Beta"]),
        make_record(movie_id=2, cast=["Gamma", "Delta"]),
    ]
    assert build(disjoint, cast_top_k=20).cast_films == {}


def test_empty_cast_is_not_an_error():
    records = [
        make_record(movie_id=1, cast=[]),
        make_record(movie_id=2, cast=[]),
    ]
    labels = build(records)
    assert labels.film_cast == {1: (), 2: ()}
    assert labels.cast_films == {}


# -- director --------------------------------------------------------------
def test_director_ignores_other_crew_jobs():
    records = [
        make_record(movie_id=1, directors=["Kenji Watanabe"]),
        make_record(
            movie_id=2,
            crew=[
                {"id": 1, "name": "Cinematographer", "job": "Director of Photography"},
                {"id": 2, "name": "Kenji Watanabe", "job": "Director"},
            ],
        ),
    ]
    labels = build(records)
    assert labels.group_for("director", 1) == frozenset({2})


def test_union_over_multiple_directors():
    records = [
        make_record(
            movie_id=1,
            crew=[
                {"id": 1, "name": "Ada", "job": "Director"},
                {"id": 2, "name": "Grace", "job": "Director"},
            ],
        ),
        make_record(movie_id=2, directors=["Grace"]),
        make_record(movie_id=3, directors=["Ada"]),
    ]
    labels = build(records)
    assert labels.group_for("director", 1) == frozenset({2, 3})


# -- identity is matched on full names, not name tokens -------------------
def test_common_first_names_do_not_create_a_director_group():
    """`dir:john` would match every director called John. Full-name matching
    must not."""
    records = [
        make_record(movie_id=1, directors=["John Smith"]),
        make_record(movie_id=2, directors=["John Williams"]),
        make_record(movie_id=3, directors=["John Smith"]),
    ]
    labels = build(records)
    assert labels.group_for("director", 1) == frozenset({3})
    assert 2 not in labels.group_for("director", 1)


# -- the processed corpus cannot supply person labels ----------------------
def test_processed_records_are_rejected_with_an_explanation():
    processed = [
        {
            "id": 1,
            "title": "Inception",
            "collection_name": None,
            "tokens": {
                "overview": ["ov:dream"],
                "cast": ["cast:leonardo", "cast:dicaprio"],
                "director": ["dir:christopher", "dir:nolan"],
            },
        }
    ]
    with pytest.raises(MissingCreditsError) as excinfo:
        build(processed)
    message = str(excinfo.value)
    assert "raw" in message.lower()
    assert "boundaries" in message.lower()


def test_shape_check_keys_on_presence_not_truthiness():
    """A raw record may legitimately carry empty credit lists."""
    records = [make_record(movie_id=1, cast=[], directors=[])]
    assert build(records).film_directors == {1: ()}


# -- catalogue restriction --------------------------------------------------
def test_catalogue_ids_excludes_films_outside_the_index():
    records = [
        make_record(movie_id=1, title="Shared"),
        make_record(movie_id=2, title="Shared"),
        make_record(movie_id=3, title="Shared"),
    ]
    labels = ProxyLabels.from_raw(
        records, cast_top_k=5, catalogue_ids=frozenset({1, 2})
    )
    # Film 3 is not indexed, so it must not appear in any group: a label can
    # never name a film the recommender is incapable of returning.
    assert 3 not in labels.same_title["shared"]
    assert labels.group_for("same_title", 1) == frozenset({2})


# -- persistence -----------------------------------------------------------
def test_round_trip_through_dict_preserves_every_label():
    records = [
        make_record(movie_id=1, title="Shared", cast=["Ada Lovelace"]),
        make_record(movie_id=2, title="Shared", cast=["Ada Lovelace"]),
        make_record(
            movie_id=3,
            title="Unique",
            directors=["Grace Hopper"],
            belongs_to_collection={"id": 1, "name": "Collection"},
        ),
        make_record(
            movie_id=4,
            title="Other",
            directors=["Grace Hopper"],
            belongs_to_collection={"id": 1, "name": "Collection"},
        ),
    ]
    labels = build(records)
    restored = ProxyLabels.from_dict(labels.to_dict())
    assert restored.franchise == labels.franchise
    assert restored.director_films == labels.director_films
    assert restored.cast_films == labels.cast_films
    assert restored.same_title == labels.same_title
    assert restored.film_titles == labels.film_titles
    assert restored.film_franchise == labels.film_franchise
    assert restored.film_directors == labels.film_directors
    assert restored.film_cast == labels.film_cast


def test_same_title_lookup_survives_a_reload():
    """Regression: the title index used to be module-level global state that
    ``from_dict`` never populated, so ``same_title`` silently returned nothing
    after a reload."""
    records = [
        make_record(movie_id=1, title="Lord of the Flies"),
        make_record(movie_id=2, title="Lord of the Flies"),
    ]
    labels = build(records)
    reloaded = ProxyLabels.from_dict(labels.to_dict())
    assert reloaded.group_for("same_title", 1) == frozenset({2})


def test_to_dict_is_byte_reproducible():
    records = [make_record(movie_id=i, title=f"F{i % 2}") for i in range(1, 5)]
    labels = build(records)
    assert labels.to_dict() == labels.to_dict()
    assert labels.sha256() == labels.sha256()


def test_write_and_read_round_trip(tmp_path):
    records = [
        make_record(movie_id=1, title="Shared"),
        make_record(movie_id=2, title="Shared"),
    ]
    path = build(records).write(tmp_path / "labels.json")
    assert ProxyLabels.read(path).same_title == {"shared": frozenset({1, 2})}


# -- summary ---------------------------------------------------------------
def test_summary_counts_groups_and_members():
    records = [
        make_record(movie_id=1, title="Shared"),
        make_record(movie_id=2, title="Shared"),
    ]
    summary = build(records).summary()
    assert summary["groups"]["same_title"] == 1
    assert summary["films_with_a_relevant_film"]["same_title"] == 2
    assert summary["groups"]["franchise"] == 0
