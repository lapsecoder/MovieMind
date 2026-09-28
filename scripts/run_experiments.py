"""
Run the Phase 4 controlled experiment matrix and write the results artifact.

Every experiment is a fixed configuration scored under one identical protocol.
Nothing is fitted or tuned: the configuration is the only thing that varies.

    python scripts/run_experiments.py
    python scripts/run_experiments.py --families genre_sweep ablation --k 10
    python scripts/run_experiments.py --quick          # 400-query smoke pass

Outputs
    data/processed/experiments.json    full metrics + geometry + provenance
    data/processed/experiment_manifest.json    reproducibility record
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moviemind.config import PreprocessConfig
from moviemind.evaluate import DEFAULT_KS, METRIC_DEFINITIONS
from moviemind.experiments import (
    build_experiment_matrix,
    experiment_manifest,
    run_experiments,
    write_manifest,
)
from moviemind.labels import ProxyLabels
from moviemind.recommend import load_corpus

PROTOCOL = (
    "Leave-one-out over the full indexed catalogue. Every film is a query; the "
    "query film is excluded from its own top-K. No train/test split exists "
    "because no model is trained: the system is fixed-configuration retrieval, "
    "so there is nothing to hold out. Proxy labels are scored only over the "
    "films for which that label defines a relevant set, and the unevaluable "
    "count is reported per label. Ties broken by ascending TMDb id, so results "
    "are bit-reproducible."
)


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", default="data/processed/movies.jsonl")
    parser.add_argument("--labels", default="data/processed/evaluation_labels.json")
    parser.add_argument("--raw", default="data/raw/tmdb_movies.jsonl")
    parser.add_argument("--out", default="data/processed/experiments.json")
    parser.add_argument("--manifest", default="data/processed/experiment_manifest.json")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--ks", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument(
        "--families",
        nargs="+",
        default=None,
        help="restrict to these experiment families",
    )
    parser.add_argument(
        "--block-mode",
        choices=("per_block", "flat"),
        default="per_block",
        help="Phase 3 measured both; per_block is the default configuration",
    )
    parser.add_argument(
        "--quick",
        type=int,
        default=0,
        metavar="N",
        help="evaluate only the first N queries, for a fast smoke pass",
    )
    parser.add_argument(
        "--samples",
        nargs="+",
        default=["rep_D"],
        help="experiment names to capture full recommendation payloads for",
    )
    args = parser.parse_args()

    processed = Path(args.processed)
    labels_path = Path(args.labels)
    if not processed.exists():
        print(f"ERROR: {processed} not found. Run scripts/build_features.py.", file=sys.stderr)
        return 1
    if not labels_path.exists():
        print(
            f"ERROR: {labels_path} not found. Run scripts/build_evaluation_labels.py.",
            file=sys.stderr,
        )
        return 1

    config = PreprocessConfig(block_mode=args.block_mode)
    corpus = load_corpus(processed)
    labels = ProxyLabels.read(labels_path)
    print(f"corpus {len(corpus)} films | block_mode={config.block_mode} | k={args.k}")
    print(f"labels {labels_path} sha256={labels.sha256()[:16]}...")

    specs = build_experiment_matrix(config)
    if args.families:
        wanted = set(args.families)
        specs = [s for s in specs if s.family in wanted]
        if not specs:
            print(f"ERROR: no experiments in families {sorted(wanted)}", file=sys.stderr)
            return 1

    families = sorted({s.family for s in specs})
    print(f"{len(specs)} experiments across {len(families)} families: {families}\n")

    query_ids = None
    if args.quick:
        # First N by catalogue order, then only the label-bearing ones, so a
        # quick pass still exercises every label path.
        query_ids = [
            int(i) for i in (m["id"] for m in corpus)
        ][: args.quick]
        print(f"QUICK PASS: {len(query_ids)} queries (not a reportable result)\n")

    results = run_experiments(
        corpus,
        labels,
        specs,
        k=args.k,
        ks=tuple(args.ks),
        query_ids=query_ids,
        collect_samples_for=args.samples,
        raw_path=args.raw,
    )

    payload = {
        "protocol": PROTOCOL,
        "k": args.k,
        "ks": sorted(set(args.ks) | {args.k}),
        "block_mode": args.block_mode,
        "corpus_films": len(corpus),
        "labels_sha256": labels.sha256(),
        "label_summary": labels.summary(),
        "metric_definitions": [
            {
                "name": m.name,
                "definition": m.definition,
                "measures": m.measures,
                "does_not_measure": m.does_not_measure,
                "requires_labels": m.requires_labels,
            }
            for m in METRIC_DEFINITIONS
        ],
        "experiments": [r.as_dict() for r in results],
        "samples": {
            r.spec.name: r.samples for r in results if r.samples
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {out}")

    manifest = experiment_manifest(
        results,
        corpus_path=str(processed),
        corpus_sha256=_file_sha256(processed),
        labels_path=str(labels_path),
        k=args.k,
        protocol=PROTOCOL,
        seed=None,
        notes=(
            "Reported run: full catalogue, per_block mode. metric_definitions "
            "travel with the results so no metric can be quoted without its "
            "caveat. Label scores are per-label and are never merged."
        ),
    )
    manifest["metric_definitions"] = payload["metric_definitions"]
    write_manifest(manifest, args.manifest)
    print(f"wrote {args.manifest}")

    _print_table(results, args.k)
    return 0


def _print_table(results, k: int) -> None:
    """Compact console summary. The JSON artifact is the real output."""
    print(f"\n{'experiment':<20} {'cov':>6} {'thin':>6} {'top1':>6} "
          f"{'fran':>6} {'dir':>6} {'cast':>6} {'title':>6} {'genres':>7}")
    print("-" * 84)
    for r in results:
        e = r.evaluation

        def rec(kind: str) -> str:
            value = e.label_recall(kind)
            return f"{value:.3f}" if value is not None else "  -  "

        print(
            f"{r.spec.name:<20} {e.coverage:>6.3f} {e.thin_evidence_rate:>6.3f} "
            f"{e.mean_top1_score:>6.3f} {rec('franchise'):>6} {rec('director'):>6} "
            f"{rec('cast'):>6} {rec('same_title'):>6} {e.distinct_genres:>7.2f}"
        )
    print(
        f"\nlabel recall@{k}; 'cov' = catalogue coverage, 'thin' = share of "
        f"recommendations on <=3 shared terms, 'genres' = distinct genres/list"
    )


if __name__ == "__main__":
    raise SystemExit(main())
