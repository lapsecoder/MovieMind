"""
Unicode handling and text normalisation.

The Phase 3 investigation found that NFC alters 0 strings in the snapshot
while NFKC alters 87 and mangles meaning ("The Naked Gun 2 1/2" ->
"The Naked Gun 21/2"). These tests pin that decision so it cannot regress.
"""

from __future__ import annotations

import pytest

from moviemind.text import (
    canonical_phrase,
    dedupe_preserving_order,
    normalise_text,
    phrase_tokens,
    tokenize,
)


# ---------------------------------------------------------------------------
# normalisation form
# ---------------------------------------------------------------------------
def test_nfc_preserves_vulgar_fraction(config):
    """NFKC would turn U+00BD into the two characters '21/2'."""
    text = "The Naked Gun 2\u00bd: The Smell of Fear"
    normalised = normalise_text(text, config)
    assert "\u00bd" in normalised
    assert "21/2" not in normalised
    assert "21\u20442" not in normalised


def test_nfc_is_idempotent(config):
    for text in ("Am\u00e9lie", "\u677f\u5c71\u4e4b\u6b87", "\u0421\u043e\u043b\u043e", "Am\u00e9lie"):
        once = normalise_text(text, config)
        assert normalise_text(once, config) == once


def test_config_declares_nfc_not_nfkc(config):
    assert config.unicode_normalization == "NFC"
    assert config.strip_accents is False


# ---------------------------------------------------------------------------
# non-Latin scripts survive intact
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected_token",
    [
        # Scripts without inter-word spaces stay one token, which is correct:
        # the token pattern finds no separator to break on.
        ("\u677f\u5c71\u4e4b\u6b87", "\u677f\u5c71\u4e4b\u6b87"),  # Chinese
        ("\u6771\u4eac\u306e\u5b50", "\u6771\u4eac\u306e\u5b50"),  # Japanese
        # Space-separated scripts split on the space and are lowercased.
        ("\u0421\u043e\u043b\u043e \u0441\u0430\u043b\u0430\u043c", "\u0441\u0430\u043b\u0430\u043c"),  # Georgian
        ("\u039a\u03bf\u03b9\u03c7\u03b9\u03c1\u03cc \u03c4\u03bf", "\u03c4\u03bf"),  # Greek
        ("\u041e\u043f\u0435\u0440\u0430\u0446\u0438\u044f", "\u043e\u043f\u0435\u0440\u0430\u0446\u0438\u044f"),  # Cyrillic
        ("\uc608\uc2dc \uc0ac\ub78c", "\uc0ac\ub78c"),  # Korean
    ],
)
def test_non_latin_tokens_are_preserved(config, text, expected_token):
    tokens = tokenize(text, config, namespace="ov")
    assert f"ov:{expected_token}" in tokens
    assert all(t.startswith("ov:") for t in tokens)
    assert all(t.islower() or not t[3:].isalpha() for t in tokens)


def test_composed_and_decomposed_forms_agree(config):
    """NFC's job is to make canonically equivalent spellings identical, so
    "Amelie" + combining acute and the precomposed form must match."""
    decomposed = "Ame\u0301lie"     # e + combining acute, unattached in the source
    precomposed = "Am\u00e9lie"
    assert normalise_text(decomposed, config) == normalise_text(precomposed, config)
    assert tokenize(decomposed, config, namespace="ov") == tokenize(
        precomposed, config, namespace="ov"
    )


def test_diacritics_are_not_folded_away(config):
    """strip_accents is False, so "Amelie" and "Amelie" stay distinct.
    13.3% of overviews and 14.5% of original_titles contain non-ASCII."""
    plain = tokenize("Amelie", config, namespace="ov")
    accented = tokenize("Am\u00e9lie", config, namespace="ov")
    assert plain == ["ov:amelie"]
    assert accented == ["ov:am\u00e9lie"]
    assert plain != accented
    assert config.strip_accents is False


def test_mixed_script_text_yields_both_scripts(config):
    text = "Am\u00e9lie \u677f\u5c71\u4e4b\u6b87"
    tokens = tokenize(text, config, namespace="ov")
    assert "ov:am\u00e9lie" in tokens
    assert "ov:\u677f\u5c71\u4e4b\u6b87" in tokens


# ---------------------------------------------------------------------------
# digits and era tokens
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["1970s", "19th century", "1600s", "post 9/11", "2040s", "3d animation", "2\u00bd"],
)
def test_numeric_and_era_tokens_survive(config, text):
    """88 keyword types in the snapshot contain digits; a numeric filter would
    delete genuine era signals."""
    assert tokenize(text, config, namespace="kw"), text


def test_keep_digits_flag_is_respected(config):
    import dataclasses

    stripped = dataclasses.replace(config, keep_digits=False)
    assert tokenize("1970s", stripped, namespace="kw") == []


# ---------------------------------------------------------------------------
# missing / hostile values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "", "   ", 123, 4.5, True, [], {}, object()])
def test_normalise_never_raises(config, value):
    assert isinstance(normalise_text(value, config), str)


def test_tokenize_none_is_empty(config):
    assert tokenize(None, config, namespace="ov") == []


def test_single_letter_initials_are_dropped_by_the_length_floor(config):
    """The token pattern excludes the separator, so "T.I." never becomes "ti";
    each initial is a single character and falls below the 2-character floor."""
    assert tokenize("T.I.", config, namespace="cast") == []
    assert tokenize("T.I.", config, namespace="cast", min_token_length=1) == [
        "cast:t",
        "cast:i",
    ]


# ---------------------------------------------------------------------------
# phrase canonicalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Science Fiction", "science fiction"),
        ("post-apocalyptic future", "post apocalyptic future"),
        ("9/11", "9 11"),
        ("hiv/aids epidemic", "hiv aids epidemic"),
        ("  spaced   out  ", "spaced out"),
    ],
)
def test_canonical_phrase_folds_punctuation_separators(config, raw, expected):
    """Hyphens and slashes are separators, so spelling variants collide."""
    assert canonical_phrase(raw, config) == expected


@pytest.mark.parametrize(
    "variant",
    ["post-apocalyptic future", "post_apocalyptic future", "Post Apocalyptic Future"],
)
def test_every_separator_variant_yields_the_same_feature(config, variant):
    """
    The observable contract is the emitted feature, not the intermediate
    canonical string. `_` is a word character, so canonical_phrase preserves it
    and phrase_tokens then rewrites the remaining spaces -- both routes end at
    the same token.
    """
    assert phrase_tokens(variant, config, namespace="kw") == ["kw:post_apocalyptic_future"]


def test_phrase_token_is_atomic_and_underscored(config):
    """A phrase token must not contain whitespace, or the vectoriser's
    whitespace analyser shreds it and leaks unnamespaced tokens."""
    (token,) = phrase_tokens("Science Fiction", config, namespace="gn")
    assert token == "gn:science_fiction"
    assert " " not in token


def test_hyphen_variants_collapse_to_one_feature(config):
    a = phrase_tokens("post-apocalyptic future", config, namespace="kw")
    b = phrase_tokens("post_apocalyptic future", config, namespace="kw")
    c = phrase_tokens("post apocalyptic future", config, namespace="kw")
    assert a == b == c == ["kw:post_apocalyptic_future"]


def test_empty_phrase_yields_no_token(config):
    assert phrase_tokens("", config, namespace="gn") == []
    assert phrase_tokens(None, config, namespace="gn") == []
    assert phrase_tokens("   ", config, namespace="gn") == []


# ---------------------------------------------------------------------------
# function-word filtering
# ---------------------------------------------------------------------------
def test_function_word_filter_is_conservative(config):
    """sklearn's list would remove these content-bearing words; the project's
    list must not."""
    for word in ("find", "after", "before", "during", "one", "two", "only", "all", "must"):
        kept = tokenize(word, config, namespace="ov", drop_function_words=True)
        assert kept == [f"ov:{word}"], word


def test_pure_function_words_are_removed(config):
    for word in ("the", "a", "of", "is", "and", "his", "her", "they", "with", "that"):
        assert tokenize(word, config, namespace="ov", drop_function_words=True) == [], word


def test_pronoun_heavy_overview_loses_most_function_words(config):
    text = "He finds her and they take it from the man who is with them."
    tokens = tokenize(text, config, namespace="ov", drop_function_words=True)
    assert "ov:he" not in tokens
    assert "ov:finds" in tokens


def test_dedupe_preserves_first_occurrence_order():
    assert dedupe_preserving_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]
