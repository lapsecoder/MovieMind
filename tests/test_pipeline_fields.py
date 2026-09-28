"""
Per-field extraction behaviour: what each block emits, and what it refuses to.
"""

from __future__ import annotations

import dataclasses

from conftest import make_record

from moviemind.config import CREDITS_STINGER_KEYWORDS
from moviemind.pipeline import (
    build_movie_features,
    extract_cast_tokens,
    extract_director_tokens,
    extract_genre_tokens,
    extract_keyword_tokens,
    extract_overview_tokens,
)


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------
def test_overview_uses_overview_namespace(config):
    tokens = extract_overview_tokens(make_record(), config)
    assert tokens
    assert all(t.startswith("ov:") for t in tokens)


def test_stub_overview_is_excluded(config):
    """18 films in the snapshot have stub overviews like 'Pakistani Film'."""
    assert extract_overview_tokens(make_record(overview="Pakistani Film"), config) == []


def test_missing_overview_yields_empty_block_not_an_error(config):
    assert extract_overview_tokens(make_record(overview=None), config) == []
    assert extract_overview_tokens(make_record(overview=""), config) == []


def test_overview_tokens_are_function_word_filtered(config):
    """The synopsis is the only field where function words are dropped."""
    record = make_record(
        overview="He finds her and they take it from the man who is with them in the house."
    )
    tokens = extract_overview_tokens(record, config)
    for function_word in ("ov:he", "ov:her", "ov:they", "ov:the", "ov:is", "ov:with"):
        assert function_word not in tokens
    assert "ov:finds" in tokens


def test_stopword_flag_changes_overview_output(config):
    record = make_record(
        overview="He finds her and they take it from the man who is with them in the house."
    )
    without = dataclasses.replace(
        config,
        overview=dataclasses.replace(config.overview, use_english_stopwords=False),
    )
    assert len(extract_overview_tokens(record, without)) > len(
        extract_overview_tokens(record, config)
    )


# ---------------------------------------------------------------------------
# keywords
# ---------------------------------------------------------------------------
def test_keywords_are_atomic_phrases(config):
    tokens = extract_keyword_tokens(make_record(keywords=["post-apocalyptic future"]), config)
    assert tokens == ["kw:post_apocalyptic_future"]


def test_keyword_spelling_variants_collapse(config):
    variants = ["post-apocalyptic future", "post_apocalyptic future", "Post Apocalyptic Future"]
    for variant in variants:
        assert extract_keyword_tokens(make_record(keywords=[variant]), config) == [
            "kw:post_apocalyptic_future"
        ]


def test_stinger_keywords_are_dropped_and_recorded(config):
    record = make_record(
        keywords=["duringcreditsstinger", "during credits stinger", "vigilante"]
    )
    removed: list[str] = []
    tokens = extract_keyword_tokens(record, config, removed=removed)
    assert tokens == ["kw:vigilante"]
    assert len(removed) == 2
    assert set(x.replace(" ", "") for x in removed) <= CREDITS_STINGER_KEYWORDS


def test_stinger_removal_is_recorded_on_the_feature_document(config):
    feature = build_movie_features(
        make_record(keywords=["aftercreditsstinger", "revenge"]), config
    )
    assert feature["stinger_keywords_removed"] == ["aftercreditsstinger"]
    assert "kw:aftercreditsstinger" not in feature["document"]


def test_a_film_with_only_stinger_keywords_gets_no_keyword_block(config):
    feature = build_movie_features(
        make_record(keywords=["beforecreditsstinger"]), config
    )
    assert feature["tokens"]["keywords"] == []


def test_missing_keywords_yield_empty_block(config):
    # An explicitly empty list, and a field that is absent/None entirely.
    assert extract_keyword_tokens(make_record(keywords=[]), config) == []
    record = make_record()
    record["keywords"] = None
    assert extract_keyword_tokens(record, config) == []


def test_malformed_keyword_entries_are_skipped(config):
    record = make_record()
    record["keywords"] = [None, "bare string", {"name": None}, {"id": 1, "name": "revenge"}]
    assert extract_keyword_tokens(record, config) == ["kw:revenge"]


# ---------------------------------------------------------------------------
# genres
# ---------------------------------------------------------------------------
def test_all_nineteen_genre_labels_fit_within_the_vocabulary(config):
    labels = [
        "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama",
        "Family", "Fantasy", "History", "Horror", "Music", "Mystery", "Romance",
        "Science Fiction", "TV Movie", "Thriller", "War", "Western",
    ]
    tokens = extract_genre_tokens(make_record(genres=labels), config)
    assert len(tokens) == 19
    assert len(set(tokens)) == 19
    assert "gn:science_fiction" in tokens
    assert "gn:tv_movie" in tokens
    assert all(" " not in t for t in tokens)


def test_genre_order_does_not_matter_for_the_set(config):
    a = extract_genre_tokens(make_record(genres=["Drama", "Comedy"]), config)
    b = extract_genre_tokens(make_record(genres=["Comedy", "Drama"]), config)
    assert set(a) == set(b) == {"gn:drama", "gn:comedy"}


def test_missing_genres_yield_empty_block(config):
    assert extract_genre_tokens(make_record(genres=[]), config) == []


# ---------------------------------------------------------------------------
# cast
# ---------------------------------------------------------------------------
def test_cast_is_ordered_by_billing_order_not_list_position(config):
    """TMDb arrives sorted, so a defensive sort must be a no-op -- which is
    exactly what makes trusting 'first K' safe. order 0 is top-billed."""
    record = make_record()
    record["cast"] = [
        {"id": 1, "name": "Zoe Zebra", "order": 0},
        {"id": 2, "name": "Ada Lovelace", "order": 1},
        {"id": 3, "name": "Bob Baker", "order": 2},
    ]
    assert extract_cast_tokens(record, config) == [
        "cast:zoe", "cast:zebra", "cast:ada", "cast:lovelace", "cast:bob", "cast:baker",
    ]


def test_cast_sort_actually_reorders_unsorted_input(config):
    """If input order were used, 'Zoe Zebra' would win the top_k cut instead."""
    record = make_record()
    record["cast"] = [
        {"id": 1, "name": "Zoe Zebra", "order": 9},
        {"id": 2, "name": "Ada Lovelace", "order": 1},
    ]
    tokens = extract_cast_tokens(record, config)
    assert tokens.index("cast:lovelace") < tokens.index("cast:zebra")


def test_cast_top_k_is_enforced(config):
    record = make_record(cast=[f"Actor{i:02d} Surname{i:02d}" for i in range(30)])
    assert config.cast.top_k == 5
    assert len(extract_cast_tokens(record, config)) == 5 * 2


def test_cast_top_k_none_keeps_everything(config):
    unbounded = dataclasses.replace(
        config, cast=dataclasses.replace(config.cast, top_k=None)
    )
    record = make_record(cast=[f"Actor{i:02d} Surname{i:02d}" for i in range(30)])
    assert len(extract_cast_tokens(record, unbounded)) == 60


def test_cast_names_are_split_into_surname_and_forename(config):
    assert extract_cast_tokens(make_record(cast=["Kenji Watanabe"]), config) == [
        "cast:kenji",
        "cast:watanabe",
    ]


def test_cast_initials_are_dropped(config):
    assert extract_cast_tokens(make_record(cast=["J. O. Strunk"]), config) == [
        "cast:strunk"
    ]


def test_duplicate_cast_names_collapse(config):
    """36 films in the snapshot repeat a name in the top-billed cast."""
    record = make_record(cast=["Ada Lovelace", "Ada Lovelace"])
    assert extract_cast_tokens(record, config) == ["cast:ada", "cast:lovelace"]


def test_missing_cast_yields_empty_block(config):
    assert extract_cast_tokens(make_record(cast=[]), config) == []
    record = make_record()
    record["cast"] = None
    assert extract_cast_tokens(record, config) == []


# ---------------------------------------------------------------------------
# director
# ---------------------------------------------------------------------------
def test_co_directors_are_all_kept(config):
    """285 films are co-directed; a scalar field would silently drop one."""
    feature = build_movie_features(
        make_record(directors=["Mark Andrews", "Brenda Chapman"]), config
    )
    tokens = feature["tokens"]["director"]
    assert "dir:andrews" in tokens
    assert "dir:chapman" in tokens
    assert "dir:mark" in tokens


def test_director_job_filter_excludes_other_crew(config):
    record = make_record(directors=["Kenji Watanabe"])
    record["crew"] = [
        {"id": 1, "name": "Kenji Watanabe", "job": "Director", "department": "Directing"},
        {"id": 2, "name": "Someone Writer", "job": "Writer", "department": "Writing"},
        {"id": 3, "name": "Someone Editor", "job": "Editor", "department": "Editing"},
    ]
    assert extract_director_tokens(record, config) == ["dir:kenji", "dir:watanabe"]


def test_director_top_k_covers_observed_maximum(config):
    assert config.director.top_k == 3


def test_missing_director_yields_empty_block(config):
    assert extract_director_tokens(make_record(directors=[]), config) == []


# ---------------------------------------------------------------------------
# document assembly
# ---------------------------------------------------------------------------
def test_document_order_is_fixed(config):
    feature = build_movie_features(
        make_record(
            overview="A detective investigates a conspiracy and a murder in a city.",
            keywords=["detective", "murder"],
            genres=["Crime", "Drama"],
            cast=["Ada Lovelace"],
            directors=["Kenji Watanabe"],
        ),
        config,
    )
    assert feature["document"] == (
        feature["tokens"]["overview"]
        + feature["tokens"]["keywords"]
        + feature["tokens"]["genres"]
        + feature["tokens"]["cast"]
        + feature["tokens"]["director"]
    )
    assert feature["document_size"] == len(feature["document"])


def test_film_with_no_text_fields_still_becomes_a_record(config):
    record = make_record(overview=None, keywords=[], genres=[], cast=[], directors=[])
    feature = build_movie_features(record, config)
    assert feature is not None
    assert feature["document"] == []
    assert all(not v for v in feature["tokens"].values())


def test_ratings_are_carried_but_never_tokenised(config):
    """Phase 1 sec 5.2 and Phase 2 sec 12: ratings inform re-ranking only."""
    feature = build_movie_features(make_record(popularity=99.5, vote_average=9.1), config)
    assert feature["popularity"] == 99.5
    assert feature["vote_average"] == 9.1
    assert not any("9.1" in t or "99.5" in t for t in feature["document"])


def test_runtime_zero_is_treated_as_unknown(config):
    assert build_movie_features(make_record(runtime=0), config)["runtime_minutes"] is None
    assert build_movie_features(make_record(runtime=120), config)["runtime_minutes"] == 120


def test_collection_name_is_preserved_not_tokenised(config):
    feature = build_movie_features(
        make_record(belongs_to_collection={"id": 1, "name": "Toy Story Collection"}), config
    )
    assert feature["collection_name"] == "Toy Story Collection"
    assert not any("toy" in t for t in feature["document"])
