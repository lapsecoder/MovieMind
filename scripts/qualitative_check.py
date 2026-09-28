"""
Qualitative sanity check across eight required categories.

Aggregate metrics cannot tell you that a film with a three-word overview is
being matched to a Bollywood epic because they share the token "love". This
script prints actual top-10 lists for one query per category, so the failure
modes are visible rather than averaged away.

    python scripts/qualitative_check.py
    python scripts/qualitative_check.py --k 10 --out data/processed/qualitative_check.json

The eight categories:

  1. popular            high-popularity, feature-rich films; the easy case
  2. obscure            low-popularity films; the case most likely to fail
  3. sparse_features    fewest non-zero terms; tests the no-evidence path
  4. no_overview        missing or stub overview; other fields must carry it
  5. documentary        non-fiction, where "story" terms behave differently
  6. animation/family   non-live-action; tests cross-medium matching
  7. same_title         shares a normalised title with another film
  8. franchise          shares a TMDb collection with another film

Note on interpretation: category 7 exists to *measure* the known-bad label, not
to show the system working. A low score there is the expected, correct result.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moviemind.config import PreprocessConfig
from moviemind.experiments import (
    SELECTED_GENRE_WEIGHT,
    SELECTED_MIN_SHARED_TERMS,
)
from moviemind.labels import ProxyLabels
from moviemind.recommend import Recommender, load_corpus

ANIMATION_FAMILY_GENRES = {"animation", "family"}


def _genres(record: dict[str, Any]) -> set[str]:
    return {
        token.split(":", 1)[1].replace("_", " ")
        for token in record["tokens"].get("genres", [])
    }


def select_queries(
    corpus: list[dict[str, Any]],
    recommender: Recommender,
    labels: ProxyLabels,
) -> list[tuple[str, int, str]]:
    """One query per category, chosen by a deterministic documented rule."""
    popularity = np.array([float(m.get("popularity") or 0.0) for m in corpus])
    nnz = np.array([recommender.row_nonzeros(i) for i in range(len(corpus))])
    chosen: dict[str, tuple[int, str]] = {}

    def take(category: str, idx: int, why: str) -> None:
        if category not in chosen:
            chosen[category] = (int(corpus[idx]["id"]), why)

    for idx in np.argsort(-popularity, kind="mergesort")[:1]:
        take("popular", int(idx), "highest TMDb popularity in the snapshot")
    for idx in np.argsort(popularity, kind="mergesort")[:1]:
        take("obscure", int(idx), "lowest TMDb popularity in the snapshot")
    for idx in np.argsort(nnz, kind="mergesort")[:1]:
        take("sparse_features", int(idx), f"fewest non-zero terms ({int(nnz[idx])})")
    for idx, record in enumerate(corpus):
        if not record["tokens"].get("overview"):
            take("no_overview", idx, "no overview tokens after stopword removal")
            break
    for idx, record in enumerate(corpus):
        if "documentary" in _genres(record):
            take("documentary", idx, "documentary genre")
            break
    for idx, record in enumerate(corpus):
        if _genres(record) & ANIMATION_FAMILY_GENRES:
            take("animation_family", idx, "animation or family genre")
            break
    for idx, record in enumerate(corpus):
        if labels.group_for("same_title", record["id"]):
            take("same_title", idx, "shares a normalised title with another film")
            break
    for idx, record in enumerate(corpus):
        group = labels.group_for("franchise", record["id"])
        if len(group) >= 3:
            take("franchise", idx, f"in a {len(group) + 1}-film TMDb collection")
            break

    order = [
        "popular",
        "obscure",
        "sparse_features",
        "no_overview",
        "documentary",
        "animation_family",
        "same_title",
        "franchise",
    ]
    return [(c, chosen[c][0], chosen[c][1]) for c in order if c in chosen]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", default="data/processed/movies.jsonl")
    parser.add_argument("--labels", default="data/processed/evaluation_labels.json")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--min-shared-terms",
        type=int,
        default=SELECTED_MIN_SHARED_TERMS,
        help="defaults to the Phase 4 selection",
    )
    parser.add_argument(
        "--genre-weight",
        type=float,
        default=SELECTED_GENRE_WEIGHT,
        help="defaults to the Phase 4 selection",
    )
    parser.add_argument("--out", default="data/processed/qualitative_check.json")
    args = parser.parse_args()

    config = PreprocessConfig()
    config = dataclasses.replace(
        config, genres=dataclasses.replace(config.genres, weight=args.genre_weight)
    )
    corpus = load_corpus(args.processed)
    labels = ProxyLabels.read(args.labels)
    recommender = Recommender.build(
        corpus, config, representation="D", min_shared_terms=args.min_shared_terms
    )

    print(
        f"representation D | genre_weight={args.genre_weight} "
        f"| min_shared_terms={args.min_shared_terms} | k={args.k}"
        f" | {recommender.dimensions} dims"
    )
    print(
        "field contribution: "
        + ", ".join(
            f"{name}={share:.1f}%"
            for name, share in recommender.field_contribution_share.items()
        )
    )
    print(f"config fingerprint {recommender.config_fingerprint}\n")
    payload: dict[str, Any] = {
        "representation": "D",
        "genre_weight": args.genre_weight,
        "min_shared_terms": args.min_shared_terms,
        "k": args.k,
        "config_fingerprint": recommender.config_fingerprint,
        "field_contribution_share_percent": recommender.field_contribution_share,
        "categories": [],
    }

    for category, movie_id, why in select_queries(corpus, recommender, labels):
        result = recommender.recommend(movie_id, args.k)
        meta = recommender.metadata(movie_id)
        group = sorted(labels.group_for("franchise", movie_id))
        print("=" * 78)
        print(f"[{category}] {result.query_title} ({movie_id}) - {why}")
        print(
            f"  year={meta['release_year']} genres={', '.join(meta['genres']) or '-'} "
            f"collection={meta['collection_name'] or '-'} "
            f"tokens={meta['document_size']} popularity={meta['popularity']}"
        )
        if group:
            print(f"  franchise siblings: {[recommender.metadata(g)['title'] for g in group]}")
        print("-" * 78)
        if not result.recommendations:
            print("  (no recommendation passed the evidence threshold)")
        for rec in result.recommendations:
            flag = " <-- franchise" if rec.movie_id in group else ""
            print(
                f"  {rec.rank:>2}. {rec.title[:44]:<44} {rec.score:.3f} "
                f"shared={rec.shared_terms:<3} {rec.year} "
                f"{','.join(rec.genres[:2])}{flag}"
            )
        if result.skipped:
            reasons: dict[str, int] = {}
            for _mid, reason in result.skipped:
                reasons[reason] = reasons.get(reason, 0) + 1
            print(f"  skipped: {reasons}")
        payload["categories"].append(
            {
                "category": category,
                "selection_rule": why,
                "query": result.as_dict(),
                "query_metadata": meta,
                "franchise_siblings": group,
            }
        )
        print()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {out}")
    print(
        "\ncategory 7 (same_title) is a known-bad proxy label and is expected to "
        "score poorly; that is a measurement of the label, not a bug."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
