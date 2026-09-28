"""
Deterministic text normalisation and tokenisation primitives.

Two invariants this module exists to guarantee:

1. **Determinism.** Given the same input string, the same tokens come out,
   in the same order, on every run and every platform. No set iteration, no
   locale-dependent behaviour, no hashing of unordered containers.

2. **Unicode preservation.** Text is normalised with NFC and otherwise left
   alone. No transliteration, no ASCII folding, no diacritic stripping. The
   snapshot has 13.3% of overviews and 14.5% of original_titles containing
   non-ASCII characters, and folding them loses information for no gain.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .config import ENGLISH_FUNCTION_WORDS, PreprocessConfig

# Pre-compiled once. `re.UNICODE` is the default for str patterns in Python 3
# and is stated explicitly because the whole design depends on it.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_DIGIT_RE = re.compile(r"\d", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)

# Characters that separate words inside a curated phrase. Folded to a single
# space so that "post-apocalyptic future", "post_apocalyptic future" and
# "post apocalyptic future" all reduce to one canonical phrase.
_PHRASE_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)


def normalise_text(value: object, config: PreprocessConfig) -> str:
    """
    Apply the project's Unicode and whitespace normalisation to a raw string.

    Handles None, numbers and non-strings without raising, because the raw
    snapshot is crowd-sourced and does contain odd values.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        # Numbers (including bool) are a legitimate, if rare, title or name.
        value = str(value)
    text = unicodedata.normalize(config.unicode_normalization, value)
    if config.lowercase:
        text = text.lower()
    return _WS_RE.sub(" ", text).strip()


def tokenize(
    value: object,
    config: PreprocessConfig,
    *,
    namespace: str | None = None,
    min_token_length: int = 2,
    drop_function_words: bool = False,
) -> list[str]:
    """
    Tokenise a free-text value into namespaced tokens.

    Digits are kept. Era descriptors ("1970s", "19th century", "1600s") are
    meaningful content signals, and 88 keyword types in the snapshot contain
    digits, so a numeric filter would delete real information.

    Single-character tokens are dropped by default: they are almost always
    name initials ("J. O. Strunk" -> "j", "o", "strunk") or articles, and
    they are uninformative as features.

    ``drop_function_words`` is applied here, on the *unprefixed* token.
    Filtering must not be delegated to a vectoriser's ``stop_words`` option,
    because that option matches whole strings and every token here carries a
    namespace prefix such as ``ov:`` -- ``stop_words="english"`` would silently
    match nothing at all.
    """
    text = normalise_text(value, config)
    if not text:
        return []
    tokens = []
    for raw in _TOKEN_RE.findall(text):
        if len(raw) < min_token_length:
            continue
        if not config.keep_digits and _DIGIT_RE.search(raw):
            continue
        if drop_function_words and raw in ENGLISH_FUNCTION_WORDS:
            continue
        tokens.append(f"{namespace}:{raw}" if namespace else raw)
    return tokens


def canonical_phrase(value: object, config: PreprocessConfig) -> str:
    """
    Reduce a curated multi-word label (a keyword or genre) to a single
    canonical string, with internal punctuation folded to single spaces.

    "post-apocalyptic future" -> "post apocalyptic future"
    "Science Fiction"         -> "science fiction"
    "9/11"                    -> "9 11"
    """
    text = normalise_text(value, config)
    if not text:
        return ""
    folded = _PHRASE_SEPARATORS.sub(" ", text)
    return _WS_RE.sub(" ", folded).strip()


def phrase_tokens(
    value: object,
    config: PreprocessConfig,
    *,
    namespace: str,
    min_token_length: int = 2,
) -> list[str]:
    """
    Emit a curated label as exactly one namespaced token.

    Used for keywords and genres. Keeping the phrase atomic preserves the
    curation TMDb applied ("based on novel or book" is a specific concept, not
    the bag of words "based on novel or book"), and folding separators means
    spelling variants of the same phrase collide intentionally.

    Internal spaces become underscores. The vectoriser splits on whitespace, so
    a token like ``gn:science fiction`` would be shredded back into
    ``gn:science`` and a bare ``fiction`` -- silently turning the 19 controlled
    genre labels into 21 features and leaking an unnamespaced token into the
    vocabulary. Underscore is a word character, survives whitespace
    tokenisation, and does not occur naturally in TMDb labels.
    """
    phrase = canonical_phrase(value, config)
    if not phrase or len(phrase) < min_token_length:
        return []
    joined = phrase.replace(" ", "_")
    return [f"{namespace}:{joined}"]


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    """
    Remove duplicates while keeping first-occurrence order.

    36 films in the snapshot credit the same person more than once, which
    would otherwise double-weight that person inside a single document.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
