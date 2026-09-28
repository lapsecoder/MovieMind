#!/usr/bin/env python
"""
Build the processed feature corpus from the raw TMDb snapshot.

    python scripts/build_features.py
    python scripts/build_features.py --config-fingerprint     # print and exit
    python scripts/build_features.py --cast-top-k 5           # variant
    python scripts/build_features.py --no-stopwords           # variant

Reads `data/raw/tmdb_movies.jsonl` (never writes to it) and produces
`data/processed/movies.jsonl` plus `data/processed/build_manifest.json`.

Deterministic: the same input and config produce a byte-identical
`movies.jsonl`. The only non-deterministic value in the manifest is
`generated_utc`.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moviemind.config import PreprocessConfig  # noqa: E402
from moviemind.pipeline import build_corpus, write_corpus  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build deterministic MovieMind features from the raw snapshot.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--raw", default=None, help="path to raw JSONL (default: config)")
    p.add_argument("--out-dir", default=None, help="output directory (default: config)")
    p.add_argument(
        "--cast-top-k",
        type=int,
        default=None,
        help="override cast truncation; measured token shares are in config.CAST_TOP_K_NOTE",
    )
    p.add_argument(
        "--keyword-min-df",
        type=int,
        default=None,
        help="override keyword min_df (1 disables pruning)",
    )
    p.add_argument(
        "--overview-min-df", type=int, default=None, help="override overview min_df"
    )
    p.add_argument(
        "--no-stopwords",
        action="store_true",
        help="keep English stopwords in the overview block (ablation)",
    )
    p.add_argument(
        "--min-overview-chars",
        type=int,
        default=None,
        help="overview length floor below which the block is skipped",
    )
    p.add_argument(
        "--config-fingerprint",
        action="store_true",
        help="print the resolved config fingerprint and exit",
    )
    p.add_argument("--quiet", action="store_true", help="suppress the summary")
    return p.parse_args(argv)


def resolve_config(args: argparse.Namespace) -> PreprocessConfig:
    config = PreprocessConfig()
    if args.raw:
        config = dataclasses.replace(config, raw_path=args.raw)
    if args.out_dir:
        config = dataclasses.replace(config, out_dir=args.out_dir)
    if args.min_overview_chars is not None:
        config = dataclasses.replace(
            config, min_overview_chars=args.min_overview_chars
        )
    if args.cast_top_k is not None:
        if args.cast_top_k < 0:
            raise SystemExit("--cast-top-k must be >= 0 (0 means unbounded)")
        config = dataclasses.replace(
            config, cast=dataclasses.replace(config.cast, top_k=args.cast_top_k or None)
        )
    if args.keyword_min_df is not None:
        config = dataclasses.replace(
            config, keywords=dataclasses.replace(config.keywords, min_df=args.keyword_min_df)
        )
    if args.overview_min_df is not None:
        config = dataclasses.replace(
            config, overview=dataclasses.replace(config.overview, min_df=args.overview_min_df)
        )
    if args.no_stopwords:
        config = dataclasses.replace(
            config,
            overview=dataclasses.replace(config.overview, use_english_stopwords=False),
        )
    return config


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = resolve_config(args)

    if args.config_fingerprint:
        print(config.fingerprint())
        return 0

    raw_path = REPO_ROOT / config.raw_path
    if not raw_path.exists():
        print(
            f"ERROR: raw snapshot not found at {raw_path}\n"
            "       see docs/phase-02-dataset-audit.md sec 14 for how to fetch it",
            file=sys.stderr,
        )
        return 2

    if not args.quiet:
        print(f"raw        : {raw_path}")
        print(f"config     : {config.fingerprint()[:16]}")

    features, report = build_corpus(raw_path, config)
    manifest = write_corpus(
        features,
        REPO_ROOT / config.out_dir,
        config,
        report=report,
        raw_path=raw_path,
    )

    corpus = manifest["corpus"]
    print()
    print(f"records    : {corpus['records']:,}")
    print(f"malformed  : {report.malformed_lines}")
    print(f"invalid id : {report.invalid_id_records}")
    print(f"dup id     : {report.duplicate_id_records}  (first occurrence kept)")
    print(f"tokens     : {corpus['total_tokens']:,}  (mean {corpus['mean_tokens_per_film']}/film)")
    print("by block   :")
    for block, share in corpus["token_share_by_block"].items():
        print(f"   {block:9s} {corpus['tokens_by_block'][block]:>8,}  {share:5.2f}%")
    print("empty block:")
    for block, count in corpus["films_with_empty_block"].items():
        print(f"   {block:9s} {count:>6,} films")
    stinger = manifest["stinger_noise"]
    print(
        f"stinger    : {stinger['films_affected']} films, "
        f"removed {', '.join(stinger['distinct_phrases_removed']) or 'none'}"
    )
    print()
    print(f"corpus     : {corpus['path']}")
    print(f"corpus sha : {corpus['sha256']}")
    print(f"raw sha    : {manifest['raw_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
