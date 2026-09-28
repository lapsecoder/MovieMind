"""
Build the structural proxy-label artifact used for evaluation.

Labels come from the **raw** TMDb snapshot, not the processed corpus: person
identity is unrecoverable from the processed token lists (see
``moviemind.labels.MissingCreditsError``). The processed corpus is used only to
restrict labelling to the 5,000 films actually indexed, so a label group can
never name a film the recommender cannot return.

    python scripts/build_evaluation_labels.py
    python scripts/build_evaluation_labels.py --cast-top-k 5 --out data/processed/evaluation_labels.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moviemind.config import PreprocessConfig
from moviemind.labels import LABEL_KINDS, ProxyLabels
from moviemind.recommend import load_corpus


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default="data/raw/tmdb_movies.jsonl")
    parser.add_argument("--processed", default="data/processed/movies.jsonl")
    parser.add_argument("--out", default="data/processed/evaluation_labels.json")
    parser.add_argument(
        "--cast-top-k",
        type=int,
        default=None,
        help="must match PreprocessConfig.cast.top_k; defaults to the config value",
    )
    args = parser.parse_args()

    raw_path = Path(args.raw)
    processed_path = Path(args.processed)
    for path in (raw_path, processed_path):
        if not path.exists():
            print(f"ERROR: {path} not found.", file=sys.stderr)
            return 1

    cast_top_k = (
        args.cast_top_k
        if args.cast_top_k is not None
        else PreprocessConfig().cast.top_k
    )
    if cast_top_k is None:
        print("ERROR: cast.top_k is unbounded; pass --cast-top-k explicitly.", file=sys.stderr)
        return 1

    print(f"raw       {raw_path}  sha256={sha256_file(raw_path)}")
    print(f"processed {processed_path}")
    print(f"cast_top_k={cast_top_k} (label coverage must not exceed the vector's)")

    catalogue = load_corpus(processed_path)
    catalogue_ids = frozenset(int(item["id"]) for item in catalogue)
    print(f"catalogue {len(catalogue_ids)} films")

    records = [
        json.loads(line)
        for line in raw_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = ProxyLabels.from_raw(
        records, cast_top_k=cast_top_k, catalogue_ids=catalogue_ids
    )

    summary = labels.summary()
    print("\nstructural proxy labels (groups of >= 2 films):")
    for kind in LABEL_KINDS:
        print(
            f"  {kind:<11} groups={summary['groups'][kind]:>5}  "
            f"films_with_a_relevant_film="
            f"{summary['films_with_a_relevant_film'][kind]:>5}"
        )

    out = labels.write(args.out)
    print(f"\nwrote {out}")
    print(f"labels sha256={labels.sha256()}")
    print(
        "\nThese are weak structural proxies from TMDb metadata, not human "
        "relevance judgements. See docs/phase-04-recommendation-evaluation.md."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
