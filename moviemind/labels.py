"""
Ground-truth proxy labels for evaluation.

**There is no relevance ground truth in this dataset.** MovieMind has no users,
no watch history, no per-user ratings and no interaction log of any kind, so
there is nothing a human ever judged to be "relevant" to anything. Every label
in this module is a *structural proxy* derived from TMDb's own editorial
metadata, and every one of them is a weak signal. See
docs/phase-04-recommendation-evaluation.md sec 2 for what each one can and
cannot support.

The three families, strongest first:

``franchise``
    Films sharing a TMDb ``belongs_to_collection`` entry. 215 groups of 2+
    films covering 528 films (James Bond x10, Dragon Ball Z x8). Strongest
    available signal: a franchise is TMDb asserting these films belong
    together, and it is *not* a restatement of genre -- though it is strongly
    correlated with it.

``director`` / ``cast``
    Other films by the same person. An auteur signal, and independent of genre
    in the sense that a director's films span genres.

``same_title``
    Films sharing a normalised title. The Phase 3 brief suggested treating all
    69 groups as remakes. Measured, that is only partly true: groups like
    ``beauty and the beast``, ``conan the barbarian``, ``fright night`` and
    ``friday the 13th`` are genuine remakes, but ``anna``, ``house``,
    ``havoc``, ``fair game`` and ``get carter`` are coincidences between
    unrelated films. 15.1% of same-title pairs share no genre at all, versus
    0.6% for franchise pairs. Treated as the weakest label.

Names are matched on the **full normalised name**, not the name *tokens*
Phase 3 stores. ``dir:john`` matches every director called John; measuring
identity on tokens inflates the director signal by 1.6x (1,654 recurring
"directors" versus 1,030 real ones).

No model is fitted to any of this. These are lookup tables used to score
retrieval output, which is evaluation, not training.
"""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

LabelKind = Literal["franchise", "director", "cast", "same_title"]

#: Ordered strongest-first. The evaluation reports each label separately and
#: never merges them into a single headline number, because they are not
#: interchangeable: a config can score well on one and badly on another, and
#: that disagreement is itself a finding.
LABEL_KINDS: tuple[LabelKind, ...] = ("franchise", "director", "cast", "same_title")

#: Only groups of at least this size define a relevant set. A singleton
#: franchise is not evidence of anything.
MIN_GROUP_SIZE = 2


class MissingCreditsError(ValueError):
    """
    Raised when person labels are requested from records that lack credits.

    Person identity is only recoverable from the **raw** snapshot. The
    processed corpus stores ``tokens['cast']`` as a flat, de-duplicated,
    order-preserving union of name tokens with no person boundaries, so
    ``cast:tom cast:hardy cast:elliot cast:page`` could be two people, one
    person, or a hyphenated surname -- the information is gone. Failing loudly
    beats silently labelling every film with one long fake "actor".
    """


def normalise_name(value: object) -> str:
    """NFC + casefold + whitespace collapse. Used to match person names."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFC", value).casefold()
    return " ".join(text.split())


@dataclass(frozen=True)
class ProxyLabels:
    """
    Immutable label lookup: group key -> member film ids.

    ``*_films`` maps a normalised person name to every film they appear in.
    ``franchise`` and ``same_title`` map a normalised collection name /
    title to member films. Only groups of ``MIN_GROUP_SIZE`` or more are kept,
    so ``relevant_films`` is never trivially empty-but-present.
    """

    franchise: Mapping[str, frozenset[int]]
    director_films: Mapping[str, frozenset[int]]
    cast_films: Mapping[str, frozenset[int]]
    same_title: Mapping[str, frozenset[int]]
    film_franchise: Mapping[int, str]
    film_directors: Mapping[int, tuple[str, ...]]
    film_cast: Mapping[int, tuple[str, ...]]
    film_titles: Mapping[int, str]

    # -- construction ----------------------------------------------------
    @classmethod
    def from_raw(
        cls,
        records: Iterable[Mapping[str, Any]],
        *,
        cast_top_k: int,
        catalogue_ids: frozenset[int] | None = None,
    ) -> ProxyLabels:
        """
        Build labels from **raw** TMDb records.

        ``cast_top_k`` must match the preprocessing config so the label set
        covers the same cast the representation can actually see. A label built
        from 20 cast members while the vector only saw 5 would credit a
        representation for information it never had.

        ``catalogue_ids`` restricts labelling to the films in the indexed
        corpus, so a label group can never name a film the recommender is
        incapable of returning.

        Raises ``MissingCreditsError`` if the records are not raw-shaped -- see
        that class for why person identity cannot be recovered from the
        processed corpus.
        """
        records = list(records)
        if records:
            _require_raw_records(records[0])

        franchise: dict[str, set[int]] = defaultdict(set)
        director_films: dict[str, set[int]] = defaultdict(set)
        cast_films: dict[str, set[int]] = defaultdict(set)
        same_title: dict[str, set[int]] = defaultdict(set)
        film_franchise: dict[int, str] = {}
        film_directors: dict[int, tuple[str, ...]] = {}
        film_cast: dict[int, tuple[str, ...]] = {}
        film_titles: dict[int, str] = {}

        for record in records:
            movie_id = record.get("id")
            if not isinstance(movie_id, int) or isinstance(movie_id, bool):
                continue
            if catalogue_ids is not None and movie_id not in catalogue_ids:
                continue

            name = _collection_name(record)
            if name:
                franchise[name].add(movie_id)
                film_franchise[movie_id] = name

            title = normalise_name(record.get("title"))
            if title:
                same_title[title].add(movie_id)
                film_titles[movie_id] = title

            directors = _director_names(record)
            film_directors[movie_id] = directors
            for person in directors:
                director_films[person].add(movie_id)

            cast = _cast_names(record, cast_top_k)
            film_cast[movie_id] = cast
            for person in cast:
                cast_films[person].add(movie_id)

        return cls(
            franchise=_keep_multi(franchise),
            director_films=_keep_multi(director_films),
            cast_films=_keep_multi(cast_films),
            same_title=_keep_multi(same_title),
            film_franchise=film_franchise,
            film_directors=film_directors,
            film_cast=film_cast,
            film_titles=film_titles,
        )

    # -- queries ---------------------------------------------------------
    def group_for(self, kind: LabelKind, movie_id: int) -> frozenset[int]:
        """
        Films that count as relevant to ``movie_id`` under ``kind``.

        Always excludes the query film itself. Returns an empty set when the
        film has no group of sufficient size, which is the normal case for
        most labels and must not be scored as a miss.
        """
        if kind == "franchise":
            key = self.film_franchise.get(movie_id)
            group = self.franchise.get(key, frozenset()) if key else frozenset()
        elif kind == "same_title":
            group = self.same_title.get(self.film_titles.get(movie_id, ""), frozenset())
        elif kind == "director":
            names = self.film_directors.get(movie_id, ())
            group = frozenset().union(
                *(self.director_films.get(n, frozenset()) for n in names)
            ) if names else frozenset()
        elif kind == "cast":
            names = self.film_cast.get(movie_id, ())
            group = frozenset().union(
                *(self.cast_films.get(n, frozenset()) for n in names)
            ) if names else frozenset()
        else:  # pragma: no cover - guarded by LABEL_KINDS
            raise ValueError(f"unknown label kind {kind!r}")
        return frozenset(group - {movie_id})

    def evaluable_films(self, kind: LabelKind, catalogue: Iterable[int]) -> list[int]:
        """Films for which ``kind`` defines at least one relevant film."""
        return [f for f in catalogue if self.group_for(kind, f)]

    def label_size(self, kind: LabelKind, movie_id: int) -> int:
        return len(self.group_for(kind, movie_id))

    def summary(self) -> dict[str, Any]:
        """Descriptive counts, for the experiment manifest."""
        out: dict[str, Any] = {"groups": {}, "films_with_a_relevant_film": {}}
        for kind, table in (
            ("franchise", self.franchise),
            ("director", self.director_films),
            ("cast", self.cast_films),
            ("same_title", self.same_title),
        ):
            members: set[int] = set()
            for group in table.values():
                members |= group
            out["groups"][kind] = len(table)
            out["films_with_a_relevant_film"][kind] = len(members)
        return out

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """JSON-ready. Keys are sorted so the artifact is byte-reproducible."""
        return {
            "min_group_size": MIN_GROUP_SIZE,
            "note": (
                "Structural proxy labels derived from TMDb metadata. Not human "
                "relevance judgements. See docs/phase-04-recommendation-evaluation.md."
            ),
            "franchise": _dump(self.franchise),
            "director_films": _dump(self.director_films),
            "cast_films": _dump(self.cast_films),
            "same_title": _dump(self.same_title),
            "film_franchise": {str(k): v for k, v in sorted(self.film_franchise.items())},
            "film_directors": {
                str(k): list(v) for k, v in sorted(self.film_directors.items())
            },
            "film_cast": {str(k): list(v) for k, v in sorted(self.film_cast.items())},
            "film_titles": {str(k): v for k, v in sorted(self.film_titles.items())},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProxyLabels:
        return cls(
            franchise=_load(payload["franchise"]),
            director_films=_load(payload["director_films"]),
            cast_films=_load(payload["cast_films"]),
            same_title=_load(payload["same_title"]),
            film_franchise={int(k): v for k, v in payload["film_franchise"].items()},
            film_directors={
                int(k): tuple(v) for k, v in payload["film_directors"].items()
            },
            film_cast={int(k): tuple(v) for k, v in payload["film_cast"].items()},
            film_titles={int(k): v for k, v in payload["film_titles"].items()},
        )

    def write(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return out

    @classmethod
    def read(cls, path: str | Path) -> ProxyLabels:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def sha256(self) -> str:
        import hashlib

        blob = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _as_dicts(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def _require_raw_records(sample: Mapping[str, Any]) -> None:
    """
    One-time check that the input is the raw snapshot, not the processed corpus.

    Key presence, not truthiness: a raw record legitimately has
    ``cast == []`` and ``crew == []``, so only the *absence* of the keys
    distinguishes the two shapes.
    """
    if "cast" in sample and "crew" in sample:
        return
    raise MissingCreditsError(
        "these records have no TMDb credit lists, so person labels cannot be "
        "built. Person identity is only recoverable from the raw snapshot: "
        "the processed corpus stores tokens['cast'] as a flat, de-duplicated "
        "union of name tokens with no person boundaries, making 'Tom Hardy' "
        "and 'Elliot Page' indistinguishable from a single four-token name. "
        "Build labels from data/raw/tmdb_movies.jsonl, and index the "
        "processed corpus."
    )


def _collection_name(record: Mapping[str, Any]) -> str:
    """Collection name from either the raw or the processed record shape."""
    collection = record.get("belongs_to_collection")
    if isinstance(collection, dict):
        return normalise_name(collection.get("name"))
    return normalise_name(record.get("collection_name"))


def _director_names(record: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Directors of a film, sorted for stable output.

    An empty credit list is a legitimate data condition, not an error: 489
    films in the snapshot have no director credit. Shape validation happens
    once, up front, in :func:`_require_raw_records`.
    """
    names = {
        normalise_name(member.get("name"))
        for member in _as_dicts(record.get("crew"))
        if member.get("job") == "Director"
    }
    return tuple(sorted(name for name in names if name))


def _cast_names(record: Mapping[str, Any], cast_top_k: int) -> tuple[str, ...]:
    """
    Top-billed cast of a film, truncated to ``cast_top_k``.

    Sorted by credit ``order`` before truncating, rather than trusting the
    snapshot's arrival order: Phase 3 verified all 5,000 lists are already
    billing-sorted, but correctness here should not depend on an upstream
    property that a future re-scrape could break. 67 films have no cast credit
    at all and correctly yield an empty tuple.
    """
    def credit_order(person: Mapping[str, Any]) -> int:
        # Uncredited / malformed entries sort last rather than first.
        value = person.get("order")
        return value if isinstance(value, int) else 1 << 30

    ordered = sorted(_as_dicts(record.get("cast")), key=credit_order)
    names = {normalise_name(person.get("name")) for person in ordered[:cast_top_k]}
    return tuple(sorted(name for name in names if name))


def _keep_multi(table: Mapping[str, set[int]]) -> dict[str, frozenset[int]]:
    return {
        key: frozenset(members)
        for key, members in sorted(table.items())
        if len(members) >= MIN_GROUP_SIZE
    }


def _dump(table: Mapping[str, frozenset[int]]) -> dict[str, list[int]]:
    return {key: sorted(members) for key, members in sorted(table.items())}


def _load(table: Mapping[str, list[int]]) -> dict[str, frozenset[int]]:
    return {key: frozenset(values) for key, values in table.items()}
