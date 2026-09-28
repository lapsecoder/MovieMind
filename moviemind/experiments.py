"""
The controlled experiment matrix for Phase 4, and its runner.

**Scope guard.** Nothing in this module fits, tunes or learns anything. Each
experiment is a fixed configuration handed to a fixed evaluation protocol;
weights are chosen by hand from measured field-contribution shares, and the
only thing that varies between runs is the configuration. That keeps the system
a retrieval tool rather than a predictive model, which is a licensing condition
(see docs/phase-02-dataset-audit.md sec 15).

Every experiment records the full provenance needed to reproduce it: config
fingerprint, representation, field set, weights, geometry and the complete
metric set. Two configurations that produce the same numbers are only
considered equivalent if their fingerprints also match.

Design constraint that shapes everything here: **the genre-weight experiment
cannot be validated against an independent label.** Every available proxy
correlates with genre (within-franchise genre Jaccard 0.775 vs 0.149 for random
pairs), so the genre sweep is reported as a trade-off curve across several
incompatible criteria, and the selection rationale is argued in the report
rather than read off a single number.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from .config import PreprocessConfig
from .evaluate import (
    DEFAULT_KS,
    EvaluationResult,
    evaluate_recommender,
)
from .labels import ProxyLabels
from .recommend import Recommender
from .representations import REPRESENTATIONS, RepresentationSpec

#: Mandated genre-weight sweep. ``0.44`` is not arbitrary: it is the weight
#: whose predicted genre energy is ~5%, and the prediction is verified against
#: the measured contribution share in every run. ``0.0`` and ``1.0`` are the
#: two ends of the trade-off -- genre as a tie-breaker, and genre dominating.
GENRE_WEIGHT_SWEEP: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.44, 0.6, 0.8, 1.0)

#: Cast is the field the Phase 3 brief required to be held down, so its bound is
#: swept as a controlled variable rather than left as an inherited default.
CAST_TOP_K_SWEEP: tuple[int, ...] = (3, 5, 10, 20)

#: Minimum shared non-zero terms a candidate must have to be returned.
#:
#: Added because measurement demanded it: 49% of top-10 recommendations at the
#: Phase 3 defaults rest on 3 or fewer shared terms, and observed top-1 matches
#: include pairs sharing a single term at cosine 0.32. The threshold is swept
#: rather than fixed so the cost in recall and coverage is measured, not guessed.
#:
#: 4 is in the sweep because the qualitative check at 3 showed the residual
#: ``shared == 3`` matches are where the visible errors live -- a James Bond
#: documentary appearing in a comedy/family list, a comedy in a horror list.
#: It is measured and **rejected**: it drives thin evidence to 0.000 but leaves
#: a mean of 9.93 surviving candidates, so a 10-item request cannot be filled
#: and the UI would have to pad. That is recorded here rather than hidden,
#: because it is the reason 3 was kept despite a non-zero thin rate.
MIN_SHARED_TERMS_SWEEP: tuple[int, ...] = (1, 2, 3, 4, 5, 8)

#: Phase 4 selection, asserted by the report and re-measured as its own
#: experiment so the shipped configuration's numbers come from a real run rather
#: than from two sweep points that were never combined.
SELECTED_GENRE_WEIGHT = 0.44
SELECTED_MIN_SHARED_TERMS = 3

ALL_FIELDS: tuple[str, ...] = ("overview", "genres", "keywords", "cast", "director")


@dataclass(frozen=True)
class ExperimentSpec:
    """One configuration to measure, plus why it exists."""

    name: str
    family: str
    hypothesis: str
    config: PreprocessConfig
    representation: RepresentationSpec | str
    #: For the report: what this experiment is meant to decide.
    question: str
    #: Post-retrieval evidence filter; see ``MIN_SHARED_TERMS_SWEEP``.
    min_shared_terms: int = 1
    #: True when the experiment needs the feature corpus rebuilt from raw
    #: rather than reusing the processed one.
    #:
    #: Required for ``cast.top_k``: the bound is applied during tokenisation in
    #: the pipeline, not during vectorisation, so varying it against an
    #: already-built corpus silently produces four identical experiments.
    #: Verified by the Phase 4 run -- cast_k3/5/10/20 came out byte-identical
    #: before this was fixed.
    rebuild_features: bool = False

    @property
    def representation_key(self) -> str:
        return (
            self.representation.key
            if isinstance(self.representation, RepresentationSpec)
            else self.representation
        )


@dataclass
class ExperimentResult:
    spec: ExperimentSpec
    evaluation: EvaluationResult
    geometry: dict[str, Any]
    seconds: float
    samples: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """
        The reproducible record.

        ``seconds`` is deliberately excluded. Wall-clock timing is the one
        non-deterministic quantity in the pipeline, and including it made two
        identical runs differ byte-for-byte, which defeats the point of hashing
        the artifact. Runtime is printed to the console instead, where it is
        useful and harmless.
        """
        return {
            "name": self.spec.name,
            "family": self.spec.family,
            "question": self.spec.question,
            "hypothesis": self.spec.hypothesis,
            "representation": self.spec.representation_key,
            "config_fingerprint": self.spec.config.fingerprint(),
            "field_weights": {
                name: cfg.weight for name, cfg in sorted(self.spec.config.fields().items())
            },
            "field_enabled": {
                name: cfg.enabled for name, cfg in sorted(self.spec.config.fields().items())
            },
            "cast_top_k": self.spec.config.cast.top_k,
            "block_mode": self.spec.config.block_mode,
            "min_df": {
                name: cfg.min_df for name, cfg in sorted(self.spec.config.fields().items())
            },
            "min_shared_terms": self.spec.min_shared_terms,
            "features_rebuilt_from_raw": self.spec.rebuild_features,
            "similarity": "cosine",
            "geometry": self.geometry,
            "metrics": self.evaluation.as_dict(),
        }


def weight_for_target_share(target: float, current: float) -> float:
    """
    Block weight that moves a field from ``current`` to ``target`` energy share.

    Block influence scales with ``weight**2``, and shares must sum to 1, so

        w = sqrt( target/(1-target) * (1-current)/current )

    Used to make the sweep interpretable in shares rather than in raw weights.
    """
    if target <= 0:
        return 0.0
    if target >= 1:
        raise ValueError("target share must be < 1 to leave the other fields mass")
    if current <= 0 or current >= 1:
        raise ValueError("current share must be in (0, 1)")
    return float(np.sqrt(target / (1 - target) * (1 - current) / current))


def _spec_for(fields: Sequence[str]) -> RepresentationSpec:
    return RepresentationSpec(
        key="custom:" + "+".join(fields),
        label=" + ".join(fields),
        fields=tuple(fields),
    )


def build_experiment_matrix(
    base: PreprocessConfig,
    *,
    genre_weights: Iterable[float] = GENRE_WEIGHT_SWEEP,
    cast_top_ks: Iterable[int] = CAST_TOP_K_SWEEP,
    min_shared_terms: Iterable[int] = MIN_SHARED_TERMS_SWEEP,
) -> list[ExperimentSpec]:
    """
    The full matrix, in report order.

    Families:
      ``representation``  Phase 3's A-D, scored end to end for the first time.
      ``genre_sweep``     the mandated genre trade-off curve.
      ``cast_top_k``      the field the brief required to be held down.
      ``ablation``        leave-one-field-out: what does each field add?
      ``single_field``    what can each field do alone?
      ``min_shared_terms`` how much shared evidence a hit must have.
      ``selected``          the shipped configuration, measured end to end.
    """
    specs: list[ExperimentSpec] = []

    # 1. Representation comparison. A is the mandatory overview-only baseline.
    for rep in REPRESENTATIONS:
        specs.append(
            ExperimentSpec(
                name=f"rep_{rep.key}",
                family="representation",
                question=(
                    "Which of the Phase 3 candidate representations retrieves "
                    "usable neighbours?"
                ),
                hypothesis=(
                    "D >= C >= B >= A on structural labels, because each added "
                    "field is a real content signal. If A ties D, the extra "
                    "fields are not earning their dimensionality."
                ),
                config=base,
                representation=rep,
            )
        )

    # 2. Genre weight sweep on D.
    for w in genre_weights:
        cfg = replace(base, genres=replace(base.genres, weight=float(w)))
        specs.append(
            ExperimentSpec(
                name=f"genre_w{w:g}",
                family="genre_sweep",
                question=(
                    "How much should genre influence similarity, given that no "
                    "independent label can validate the answer?"
                ),
                hypothesis=(
                    "Franchise/director/cast label recall peaks at moderate "
                    "genre weight and falls at 0.0, while distinct-genre "
                    "breadth falls monotonically as weight rises. The optimum "
                    "for a 'films like this' tool is the knee, not the maximum."
                ),
                config=cfg,
                representation=_spec_for(ALL_FIELDS),
            )
        )

    # 3. Cast top_k sweep on D. Rebuilds features: `top_k` is a tokenisation
    #    bound, so the processed corpus cannot express this variation.
    for k in cast_top_ks:
        cfg = replace(base, cast=replace(base.cast, top_k=int(k)))
        specs.append(
            ExperimentSpec(
                name=f"cast_k{k}",
                family="cast_top_k",
                question="How many cast credits are worth indexing?",
                hypothesis=(
                    "Cast recall rises then plateaus with k, while thin-evidence "
                    "matches rise monotonically, because a larger ensemble "
                    "matches more unrelated films on one shared actor."
                ),
                config=cfg,
                representation=_spec_for(ALL_FIELDS),
                rebuild_features=True,
            )
        )

    # 4. Leave-one-field-out ablations on D.
    for drop in ALL_FIELDS:
        kept = tuple(f for f in ALL_FIELDS if f != drop)
        specs.append(
            ExperimentSpec(
                name=f"drop_{drop}",
                family="ablation",
                question=f"What does the {drop} field contribute on its own?",
                hypothesis=(
                    f"Removing {drop} degrades the labels it structurally "
                    "explains (franchise for overview/keywords, cast label for "
                    "cast, director label for director) and leaves others flat. "
                    "A field that degrades nothing is carrying dimensionality "
                    "for free."
                ),
                config=base,
                representation=_spec_for(kept),
            )
        )

    # 5. Single field alone, for the floor each signal can reach.
    for only in ALL_FIELDS:
        specs.append(
            ExperimentSpec(
                name=f"only_{only}",
                family="single_field",
                question=f"How far does the {only} field get on its own?",
                hypothesis=(
                    "Every single field retrieves something non-zero except "
                    "genres at equal weight, whose 21 dimensions are swamped "
                    "by name collisions; genre needs an explicit weight to be "
                    "usable at all."
                ),
                config=base,
                representation=_spec_for((only,)),
            )
        )

    # 6. Evidence-threshold sweep on D. Post-retrieval, so the feature space is
    #    held constant and only the acceptance rule varies.
    for threshold in min_shared_terms:
        specs.append(
            ExperimentSpec(
                name=f"evidence_mt{threshold}",
                family="min_shared_terms",
                question=(
                    "How much shared evidence should a recommendation require "
                    "before it is shown to a user?"
                ),
                hypothesis=(
                    "Raising the threshold cuts thin-evidence matches and "
                    "raises mean top-1 score, while coverage and label recall "
                    "fall. The defensible setting is the smallest threshold "
                    "that materially reduces thin matches, not the largest one "
                    "the metrics tolerate."
                ),
                config=base,
                representation=_spec_for(ALL_FIELDS),
                min_shared_terms=threshold,
            )
        )

    # 7. The shipped configuration, measured end to end.
    specs.append(
        ExperimentSpec(
            name="selected",
            family="selected",
            question=(
                "What does the configuration this report actually recommends "
                "score?"
            ),
            hypothesis=(
                "Combining the genre weight and evidence threshold chosen from "
                "the sweeps reproduces their individual effects, because the "
                "two act on different stages: genre changes the vector, the "
                "threshold changes which retrieved rows are shown."
            ),
            config=replace(
                base, genres=replace(base.genres, weight=SELECTED_GENRE_WEIGHT)
            ),
            representation=_spec_for(ALL_FIELDS),
            min_shared_terms=SELECTED_MIN_SHARED_TERMS,
        )
    )

    return specs


def popularity_percentiles(corpus: Sequence[dict[str, Any]]) -> dict[int, float]:
    """
    Map TMDb id -> popularity percentile in [0, 100].

    Percentile rather than raw popularity because the snapshot's popularity is
    heavily right-skewed (Phase 2 sec 15): a raw-value correlation would be
    dominated by a handful of blockbusters. Ties get the average rank, so two
    films with identical popularity are not treated as different.
    """
    values = np.array(
        [float(item.get("popularity") or 0.0) for item in corpus], dtype=np.float64
    )
    n = len(values)
    if n == 0:
        return {}
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.arange(n, dtype=np.float64)
    sorted_values = values[order]
    start = 0
    for i in range(1, n + 1):
        if i == n or sorted_values[i] != sorted_values[start]:
            if i - start > 1:
                ranks[order[start:i]] = (start + i - 1) / 2.0
            start = i
    out: dict[int, float] = {}
    for item, pct in zip(corpus, ranks / max(n - 1, 1) * 100.0):
        out[int(item["id"])] = float(pct)
    return out


def run_experiments(
    corpus: Sequence[dict[str, Any]],
    labels: ProxyLabels,
    specs: Sequence[ExperimentSpec],
    *,
    k: int = 10,
    ks: Iterable[int] = DEFAULT_KS,
    query_ids: Sequence[int] | None = None,
    collect_samples_for: Iterable[str] = (),
    raw_path: str | Path | None = None,
    progress: bool = True,
) -> list[ExperimentResult]:
    """
    Run every spec under one protocol and return the results in order.

    The corpus, labels, protocol and ``k`` are held constant across
    configurations -- that is the entire point. Only the configuration varies.

    ``query_ids`` restricts the query set for a quick pass. Leave it unset for
    the full catalogue, which is the protocol the report uses.

    ``raw_path`` is required if any spec sets ``rebuild_features`` (the
    ``cast_top_k`` family). Rebuilds are cached per config fingerprint, so
    repeated specs over the same bound cost one tokenisation pass.
    """
    percentiles = popularity_percentiles(corpus)
    sample_names = set(collect_samples_for)
    results: list[ExperimentResult] = []
    rebuild_cache: dict[str, list[dict[str, Any]]] = {}

    for position, spec in enumerate(specs, start=1):
        started = time.perf_counter()
        if progress:
            print(
                f"[{position}/{len(specs)}] {spec.name} "
                f"({spec.family}, {spec.representation_key})",
                flush=True,
            )
        features: Sequence[dict[str, Any]] = corpus
        if spec.rebuild_features:
            if raw_path is None:
                raise ValueError(
                    f"experiment {spec.name!r} sets rebuild_features, so "
                    "raw_path is required"
                )
            fingerprint = spec.config.fingerprint()
            if fingerprint not in rebuild_cache:
                from .pipeline import build_corpus

                rebuilt, report = build_corpus(raw_path, spec.config)
                if report.records_parsed != len(corpus):
                    raise ValueError(
                        f"rebuilt corpus has {report.records_parsed} films but the "
                        f"indexed corpus has {len(corpus)}; the label catalogue "
                        "and the feature corpus must describe the same films"
                    )
                rebuild_cache[fingerprint] = rebuilt
            features = rebuild_cache[spec.config.fingerprint()]

        recommender = Recommender.build(
            features, spec.config, representation=spec.representation
        )
        evaluation = evaluate_recommender(
            recommender,
            labels,
            name=spec.name,
            k=k,
            ks=ks,
            query_ids=query_ids,
            popularity_percentiles=percentiles,
            min_shared_terms=spec.min_shared_terms,
        )
        geometry = {
            "films": recommender.size,
            "dimensions": recommender.dimensions,
            "nonzeros": recommender.nonzeros,
            "density_percent": round(recommender.density_percent, 4),
            "field_contribution_share_percent": recommender.field_contribution_share,
        }
        samples: list[dict[str, Any]] = []
        if spec.name in sample_names and query_ids is None:
            samples = _collect_samples(recommender, labels, query_ids, k)
        results.append(
            ExperimentResult(
                spec=spec,
                evaluation=evaluation,
                geometry=geometry,
                seconds=time.perf_counter() - started,
                samples=samples,
            )
        )
        if progress:
            rec = evaluation.label_recall("franchise")
            print(
                f"    coverage={evaluation.coverage:.3f} "
                f"thin={evaluation.thin_evidence_rate:.3f} "
                f"franchise_recall@{k}="
                f"{rec if rec is None else f'{rec:.4f}'} "
                f"({results[-1].seconds:.1f}s)",
                flush=True,
            )
    return results


def _collect_samples(
    recommender: Recommender,
    labels: ProxyLabels,
    query_ids: Sequence[int] | None,
    k: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Full recommendation payloads for the first ``limit`` label-bearing queries.

    Sampled in catalogue order, not at random, so the output is reproducible
    and re-runnable without recording a seed for a selection that does not need
    one.
    """
    out: list[dict[str, Any]] = []
    for movie_id in recommender.movie_ids.tolist():
        if not labels.group_for("franchise", movie_id):
            continue
        try:
            result = recommender.recommend(movie_id, k)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            out.append({"query_id": movie_id, "error": str(exc)})
            continue
        payload = result.as_dict()
        payload["franchise_group"] = sorted(
            labels.group_for("franchise", movie_id)
        )
        out.append(payload)
        if len(out) >= limit:
            break
    return out


def experiment_manifest(
    results: Sequence[ExperimentResult],
    *,
    corpus_path: str,
    corpus_sha256: str,
    labels_path: str,
    k: int,
    protocol: str,
    seed: int | None,
    notes: str = "",
) -> dict[str, Any]:
    """
    The reproducibility record. Written alongside every results file.

    ``seed`` is ``None`` for the reported runs: the protocol is fully
    deterministic (no sampling, no shuffling, id-based tie-breaks), so there is
    nothing for a seed to control. It is recorded as ``null`` rather than a
    fabricated number so the absence is explicit.
    """
    return {
        "generated_by": "python scripts/run_experiments.py",
        "corpus_path": corpus_path,
        "corpus_sha256": corpus_sha256,
        "labels_path": labels_path,
        "similarity": "cosine (dot product over L2-normalised TF-IDF)",
        "top_k": k,
        "protocol": protocol,
        "seed": seed,
        "determinism": (
            "Fully deterministic. No sampling, no shuffling; ties broken by "
            "ascending TMDb id. Re-running any experiment reproduces it exactly."
        ),
        "notes": notes,
        "experiments": [r.as_dict() for r in results],
    }


def write_manifest(payload: dict[str, Any], path: str | Path) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
