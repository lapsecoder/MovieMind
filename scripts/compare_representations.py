#!/usr/bin/env python
"""
Compare candidate feature representations A/B/C/D.

    python scripts/compare_representations.py
    python scripts/compare_representations.py --save-matrices

Reports, per representation: dimensionality, sparsity, per-block breakdown,
field contribution share (which field controls how much of the vector), and
document coverage.

Analysis only. This script does not compute similarities, rank neighbours, or
produce recommendations -- that is Phase 4.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import scipy.sparse as sp

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moviemind.config import PreprocessConfig  # noqa: E402
from moviemind.representations import (  # noqa: E402
    REPRESENTATIONS,
    RepresentationResult,
    build_representation,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compare candidate MovieMind feature representations.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--corpus", default="data/processed/movies.jsonl")
    p.add_argument("--out-dir", default="data/processed/representations")
    p.add_argument("--json", default=None, help="also write the report to this path")
    p.add_argument(
        "--save-matrices",
        action="store_true",
        help="persist each representation's TF-IDF matrix as .npz",
    )
    p.add_argument(
        "--only",
        default=None,
        help="restrict to one representation key, e.g. D",
    )
    p.add_argument(
        "--block-mode",
        default=PreprocessConfig().block_mode,
        choices=("per_block", "flat"),
        help="how fields are combined: equal-weight blocks, or one flat vector",
    )
    p.add_argument(
        "--label",
        default=None,
        help="suffix for the default --json filename, so each mode gets its own file",
    )
    return p.parse_args(argv)


def load_corpus(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"ERROR: processed corpus not found at {path}\n"
            "       run: python scripts/build_features.py"
        )
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _bar(percent: float, width: int = 24) -> str:
    filled = int(round(percent / 100.0 * width))
    return "#" * filled + "." * (width - filled)


def print_table(results: list[RepresentationResult]) -> None:
    header = f"{'':4s} {'representation':44s} {'dims':>7s} {'nnz':>9s} {'density':>8s}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.spec.key:4s} {r.spec.label:44s} {r.dimensions:7,d} "
            f"{r.nonzeros:9,d} {r.density_percent:7.3f}%"
        )

    print()
    print("per-block dimensions")
    print("-" * 72)
    block_names: list[str] = []
    for r in results:
        for b in r.blocks:
            if b.name not in block_names:
                block_names.append(b.name)
    print(f"{'':4s} " + " ".join(f"{n:>12s}" for n in block_names))
    for r in results:
        cells = []
        for name in block_names:
            block = next((b for b in r.blocks if b.name == name), None)
            cells.append(f"{block.dimensions:12,d}" if block else " " * 12)
        print(f"{r.spec.key:4s} " + " ".join(cells))

    print()
    print("field contribution share (% of total squared L2 mass)")
    print("-" * 72)
    print(f"{'':4s} " + " ".join(f"{n:>10s}" for n in block_names))
    for r in results:
        share = r.field_contribution_share()
        cells = " ".join(
            f"{share.get(n, 0.0):9.2f}%" if n in share else " " * 11 for n in block_names
        )
        print(f"{r.spec.key:4s} " + cells)

    print()
    print("documents carrying each field (of %d)" % results[0].n_documents)
    print("-" * 72)
    print(f"{'':4s} " + " ".join(f"{n:>12s}" for n in block_names))
    for r in results:
        cover = r.per_document_coverage()
        cells = " ".join(f"{cover.get(n, 0):12,d}" for n in block_names)
        print(f"{r.spec.key:4s} " + cells)
    print()
    for r in results:
        print(
            f"{r.spec.key}: all-zero documents = {r.all_zero_documents}, "
            f"mean nnz/doc = {r.nonzeros / max(r.n_documents, 1):.2f}"
        )


def save_matrices(
    results: list[RepresentationResult], out_dir: Path, block_mode: str
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        path = out_dir / f"tfidf_{r.spec.key}_{block_mode}.npz"
        sp.save_npz(path, r.matrix)
        meta = {
            "key": r.spec.key,
            "label": r.spec.label,
            "block_mode": block_mode,
            "documents": r.n_documents,
            "dimensions": r.dimensions,
            "feature_names": r.feature_names,
        }
        (out_dir / f"tfidf_{r.spec.key}_{block_mode}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"saved {path.name}: {r.matrix.shape} ({path.stat().st_size:,} bytes)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    corpus = load_corpus(REPO_ROOT / args.corpus)
    config = replace(PreprocessConfig(), block_mode=args.block_mode)

    specs = REPRESENTATIONS
    if args.only:
        wanted = args.only.strip().upper()
        specs = tuple(s for s in specs if s.key == wanted)
        if not specs:
            raise SystemExit(f"unknown representation key {args.only!r}; use A, B, C or D")

    print(f"corpus        : {args.corpus}")
    print(f"documents     : {len(corpus):,}")
    print(f"config        : {config.fingerprint()[:16]}")
    print(f"block mode    : {config.block_mode}")
    print(f"representations: {', '.join(s.key for s in specs)}")
    print()

    results = []
    for spec in specs:
        result = build_representation(corpus, spec, config)
        results.append(result)
        print(f"built {spec.key}: {result.dimensions:,} dims, {result.nonzeros:,} nnz")

    print()
    print_table(results)

    if args.save_matrices:
        print()
        save_matrices(results, REPO_ROOT / args.out_dir, args.block_mode)

    report = {
        "config_fingerprint": config.fingerprint(),
        "block_mode": config.block_mode,
        "documents": len(corpus),
        "representations": [r.as_dict() for r in results],
    }
    json_path = args.json
    if json_path is None and args.label:
        json_path = f"{args.out_dir}/report_{args.label}.json"
    if json_path:
        path = REPO_ROOT / json_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
