"""
Frozen preprocessing configuration for MovieMind Phase 3.

Every value here is a decision, not a default. Each carries the measured
evidence that justifies it (see docs/phase-03-feature-engineering.md).
Changing anything in this file changes the feature space, so the whole
config is hashed into the build manifest to make runs comparable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

#: The four Unicode normalisation forms `unicodedata.normalize` accepts. Typed
#: as a Literal rather than a bare `str` so that a typo such as "NFE" is a type
#: error at the call site rather than a runtime ValueError in the middle of a
#: 5,000-film build. Phase 3 sec 2.1 justifies picking NFC from these four.
UnicodeNormalizationForm = Literal["NFC", "NFD", "NFKC", "NFKD"]

# Feature namespace prefixes. Every token carries one so that identical
# surface forms from different fields cannot collide.
#
# Measured justification (5,000-film snapshot):
#   overview INTERSECT cast      = 4,226 shared tokens ("aaron", "abby", ...)
#   overview INTERSECT keywords  = 5,708 shared tokens
#   overview INTERSECT genres    = 21    (all of them)
# Unprefixed union = 56,889 terms; namespaced = 69,730 (+23%). The 23% cost
# buys field isolation and per-block min_df / weighting, which is the main
# quality lever available (cf. Phase 1 sec 8.1).
NS_OVERVIEW = "ov"
NS_KEYWORD = "kw"
NS_GENRE = "gn"
NS_CAST = "cast"
NS_DIRECTOR = "dir"

ALL_NAMESPACES = (NS_OVERVIEW, NS_KEYWORD, NS_GENRE, NS_CAST, NS_DIRECTOR)

# TMDb credits-stinger keywords. These describe post-roll content rather than
# the film, and they are actively harmful as similarity features: 275 films
# (5.5% of the snapshot) carry one, and they would make unrelated films look
# alike. Matched case-insensitively against the *normalised* keyword phrase.
# Source: Phase 2 sec 9.
CREDITS_STINGER_KEYWORDS = frozenset(
    {
        "duringcreditsstinger",
        "aftercreditsstinger",
        "beforecreditsstinger",
    }
)

# Conservative English function-word list, applied to the overview block only.
#
# NOT sklearn's `stop_words="english"`. That list removes words which carry
# real descriptive weight in a plot summary; measured document frequencies in
# the snapshot: find 397, after 820, one 631, two 473, three 249, only 353,
# all 447, during 236, before 225, more 233, other 252, must 393, will 374.
# Those are exactly the words that help distinguish one film from another.
#
# The list below is restricted to closed-class function words: articles,
# prepositions, auxiliaries, coordinating/subordinating conjunctions, relative
# pronouns, and personal pronouns. Personal pronouns matter here: `his`
# (df 3,749), `her` (2,104), `he` (2,061) and `they` (1,069) are among the
# most frequent tokens in the corpus and carry no film-specific information.
#
# Deliberately retained as content-bearing: temporal and quantifier words
# (after/before/during/one/two/three/all/only/more/most/other), aspect and
# modal verbs (find/must/will/can/has), and adverbs (up/out/off/down/too/very).
ENGLISH_FUNCTION_WORDS = frozenset(
    ["a", "an", "the", "and", "or", "but", "nor", "so", "yet", "if", "because", "as", "until", "while", "whereas", "although", "though", "since", "unless", "when", "where", "whether", "that", "which", "who", "whom", "whose", "of", "in", "on", "at", "to", "for", "with", "without", "from", "into", "onto", "upon", "about", "against", "between", "among", "over", "under", "above", "below", "by", "through", "is", "are", "was", "were", "be", "been", "being", "am", "has", "have", "had", "do", "does", "did", "it", "its", "he", "him", "his", "she", "her", "hers", "they", "them", "their", "theirs", "we", "us", "our", "ours", "you", "your", "yours", "i", "me", "my", "mine", "not", "no"]
)


@dataclass(frozen=True)
class FieldConfig:
    """Per-field extraction and vectorisation settings."""

    namespace: str
    enabled: bool = True
    # Absolute document frequency floor. 1 disables pruning.
    min_df: int = 2
    # English stopword list. Applied to free prose ONLY. Curated keyword and
    # genre vocabularies keep words like "war", "love", "man" that are
    # genuinely informative there (Phase 1 sec 8.2).
    use_english_stopwords: bool = False
    # Multipliers applied to the block's unit vector before concatenation.
    weight: float = 1.0
    # Person-name blocks only: keep at most this many credits, in billing
    # order. See CAST_TOP_K_NOTE.
    top_k: int | None = None
    # Minimum length for a token emitted from this field.
    min_token_length: int = 2


@dataclass(frozen=True)
class PreprocessConfig:
    """
    The complete, reproducible definition of MovieMind's feature space.

    Defaults are the Phase 3 recommendations. Instantiate with overrides to
    build a comparison variant; the object is hashable via ``fingerprint``.
    """

    # ---- text normalisation -------------------------------------------------
    # NFC, not NFKC. Measured: NFC alters 0 strings in the snapshot, NFKC
    # alters 87 and mangles meaning, e.g. "The Naked Gun 2 1/2" becomes
    # "The Naked Gun 21/2". Compatibility folding is actively harmful here.
    unicode_normalization: UnicodeNormalizationForm = "NFC"
    lowercase: bool = True
    # Diacritics are preserved. Transliteration was rejected: 13.3% of
    # overviews and 14.5% of original_titles contain non-ASCII characters and
    # folding them destroys the distinction between e.g. "Amelie" and
    # "Amélie" while adding no retrievable signal.
    strip_accents: bool = False

    # ---- tokenisation -------------------------------------------------------
    # Unicode word characters, keeping digits. Era tokens such as "1970s",
    # "19th century" and "1600s" are genuine signals: 88 keyword types contain
    # digits, so a "drop numeric tokens" rule would destroy real information.
    token_pattern: str = r"[^\W_]+"
    keep_digits: bool = True

    # ---- per-field settings -------------------------------------------------
    overview: FieldConfig = field(
        default_factory=lambda: FieldConfig(
            namespace=NS_OVERVIEW,
            min_df=2,
            use_english_stopwords=True,   # 14.51% of vector mass, all low-IDF
            min_token_length=2,
        )
    )
    keywords: FieldConfig = field(
        default_factory=lambda: FieldConfig(
            namespace=NS_KEYWORD,
            # Provably lossless for pairwise cosine: a keyword in exactly one
            # film has a single non-zero in its column and therefore cannot
            # contribute to any off-diagonal similarity. Removes 53.2% of the
            # keyword vocabulary at zero cost.
            min_df=2,
            use_english_stopwords=False,
            min_token_length=2,
        )
    )
    genres: FieldConfig = field(
        default_factory=lambda: FieldConfig(
            namespace=NS_GENRE,
            min_df=1,          # only 19 types; pruning would be arbitrary
            use_english_stopwords=False,
            min_token_length=2,
        )
    )
    cast: FieldConfig = field(
        default_factory=lambda: FieldConfig(
            namespace=NS_CAST,
            min_df=2,          # only 27.0% of people recur; rest are unmatchable
            use_english_stopwords=False,
            min_token_length=2,  # drops name initials ("J. O." -> J, O)
            top_k=5,
        )
    )
    director: FieldConfig = field(
        default_factory=lambda: FieldConfig(
            namespace=NS_DIRECTOR,
            min_df=2,
            use_english_stopwords=False,
            min_token_length=2,
            top_k=3,           # 285 films are co-directed; keep them all
        )
    )

    # ---- document assembly --------------------------------------------------
    # A film whose overview is missing still gets a document built from its
    # other fields rather than being dropped. 100% of the snapshot keeps a
    # non-empty document.
    require_nonempty_document: bool = True
    # Overview shorter than this is treated as a stub ("Pakistani Film") and
    # excluded from the overview block. 18 films affected (0.36%).
    min_overview_chars: int = 40

    # ---- vectoriser ---------------------------------------------------------
    sublinear_tf: bool = True
    # How multiple fields are combined. Both modes are measured and reported
    # in Phase 3; neither is assumed optimal.
    #
    #   "per_block" -- each field is vectorised and L2-normalised alone, then
    #     weighted and concatenated. Gives exact control over a field's share
    #     of the vector, but with equal weights it hands the 21-dimension genre
    #     block the same energy as the 10,571-dimension overview block, which
    #     measurably over-weights genres (50.3% of energy in representation B).
    #
    #   "flat" -- all fields are concatenated into one namespaced document and
    #     vectorised once, so a field's influence follows its share of tokens
    #     (overview 56.6%, cast 25.3%, keywords 12.1% at top_k=10). Simpler,
    #     but lets cast reach a quarter of the vector.
    #
    # Field influence scales with weight**2, so a target share s from a
    # current share s0 needs weight *= sqrt(s/(1-s) * (1-s0)/s0).
    block_mode: str = "per_block"
    per_block_l2: bool = True
    final_l2: bool = True

    # ---- keyword shaping ----------------------------------------------------
    # Keywords are emitted as one atomic namespaced token with internal
    # punctuation folded to spaces, e.g. "post-apocalyptic future" and
    # "post apocalyptic future" collapse to the same feature. Splitting
    # keywords into words was measured and rejected: it adds +27% features
    # that are largely redundant with the overview block.
    keyword_as_phrase: bool = True

    # ---- provenance ---------------------------------------------------------
    raw_path: str = "data/raw/tmdb_movies.jsonl"
    out_dir: str = "data/processed"

    def fields(self) -> Mapping[str, FieldConfig]:
        return {
            "overview": self.overview,
            "keywords": self.keywords,
            "genres": self.genres,
            "cast": self.cast,
            "director": self.director,
        }

    def enabled_fields(self) -> dict[str, FieldConfig]:
        return {k: v for k, v in self.fields().items() if v.enabled}

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        """Stable hash of the whole configuration, for the build manifest."""
        blob = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Rationale string kept next to the constant it explains so the two cannot
# drift apart.
#
# NOTE: the first table is measured on RAW whitespace tokens, before the
# overview block has its function words removed. The second table is the share
# that actually reaches the vectoriser, i.e. after that removal shrank the
# overview from 219,755 to 140,197 tokens. Both are reported because the
# denominator moved, and quoting only one of them would be misleading.
CAST_TOP_K_NOTE = """
Cast is bounded so a large ensemble cannot outweigh the synopsis.

Billing order is trustworthy: all 5,000/5,000 cast lists arrive already sorted
ascending by `order`, so "first K" genuinely means top-billed. The audit also
found no junk-credit pattern (no "Self", "Extra" or uncredited placeholders),
and the snapshot's own cap is 20, so top_k=None is identical to top_k=20.

Share of RAW field tokens (mean 102.49 whitespace tokens/film):
  top- 3 ->  5.9%    top- 8 -> 15.7%    top-20 -> 36.0%   <- undominated input
  top- 5 ->  9.9%    top-10 -> 19.4%

Share of PROCESSED tokens (mean 61.77 tokens/film, after overview stopwords):
  top_k   tokens/film   cast   overview  keywords  genres  director
      3        48.22   12.67%    58.15%     19.49%   5.11%     4.58%
      5        52.21   19.34%    53.71%     18.00%   4.72%     4.23%   <- chosen
      8        58.03   27.44%    48.32%     16.20%   4.25%     3.80%
     10        61.77   31.82%    45.40%     15.22%   3.99%     3.57%
     20        77.93   45.96%    35.98%     12.06%   3.16%     2.83%

Why 5: the overview is the primary content signal, so it should stay the
largest contributor; keywords are curated by TMDb and are the strongest
secondary signal; cast is precise but a supporting one. top_k=5 is the value
that actually holds cast below a fifth of the document once the overview has
been stripped of function words. top_k=10 was chosen from the raw-token table
and turned out to make cast (31.8%) larger than keywords (15.2%).

Cast coverage is unaffected: a film with at least one credited actor still
contributes cast tokens at any top_k. Only the vocabulary shrinks, and the
shrunk tail is dominated by one-off names that min_df=2 removes anyway.

Field importance is the job of `weight`, not `top_k`. See block_mode notes.
"""
