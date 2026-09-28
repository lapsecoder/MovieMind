"""
Candidate feature representations, built for *analysis only*.

Four representations are compared, as required by the Phase 3 brief:

    A  overview
    B  overview + genres
    C  overview + genres + keywords
    D  overview + genres + keywords + controlled cast/director

Each is a concatenation of independently vectorised, L2-normalised, weighted
blocks. Per-block normalisation is what makes a field's influence a function of
its configured weight rather than of its raw token count -- the direct answer
to the requirement that cast must not dominate the representation.

**Scope guard.** This module produces feature matrices and their descriptive
statistics. It deliberately does not compute a similarity matrix, rank
neighbours, or produce recommendations; that is Phase 4.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy.sparse as sp
from sklearn.feature_extraction.text import TfidfVectorizer

from .config import FieldConfig, PreprocessConfig


@dataclass(frozen=True)
class RepresentationSpec:
    """A named subset of fields to vectorise together."""

    key: str
    label: str
    fields: tuple[str, ...]


REPRESENTATIONS: tuple[RepresentationSpec, ...] = (
    RepresentationSpec("A", "overview", ("overview",)),
    RepresentationSpec("B", "overview + genres", ("overview", "genres")),
    RepresentationSpec(
        "C", "overview + genres + keywords", ("overview", "genres", "keywords")
    ),
    RepresentationSpec(
        "D",
        "overview + genres + keywords + cast + director",
        ("overview", "genres", "keywords", "cast", "director"),
    ),
)


# Keyword and genre blocks are already token lists stored on each record, so
# they are joined with spaces and re-tokenised by the vectoriser's whitespace
# analyser. Because every token already carries its namespace prefix and the
# canonicaliser has folded phrase punctuation, this is a lossless round trip.
def _block_texts(
    features: Sequence[Mapping[str, Any]], field_name: str
) -> list[str]:
    return [" ".join(item["tokens"].get(field_name, [])) for item in features]


def _vectorizer_for(field_cfg: FieldConfig, config: PreprocessConfig) -> TfidfVectorizer:
    return TfidfVectorizer(
        # Tokens are pre-namespaced and pre-canonicalised, so a whitespace
        # analyser is the correct, and fastest, choice here.
        analyzer="word",
        token_pattern=r"(?u)\S+",
        lowercase=False,          # already lowercased upstream
        min_df=field_cfg.min_df,
        max_df=1.0,
        # Phase 1 sec 8.3: sublinear tf + L2 makes cosine a plain dot product.
        sublinear_tf=config.sublinear_tf,
        norm="l2" if config.per_block_l2 else None,
        # Stopwords are already removed upstream, in the pipeline, on the
        # unprefixed token. Passing them here would be a no-op: every token
        # carries a namespace prefix, so a whole-string match never fires.
        stop_words=None,
        smooth_idf=True,
        # Fixed vocabulary ordering keeps output deterministic.
        dtype=np.float32,
    )


@dataclass
class BlockResult:
    name: str
    matrix: sp.csr_matrix
    vocabulary: list[str]
    weight: float
    documents_with_tokens: int
    # Column indices this block occupies in the *final* representation matrix.
    # Required, not an optimisation: per_block concatenates contiguously, but
    # flat mode builds one vocabulary sorted alphabetically, so a field's
    # columns are scattered. Deriving the slice from block widths alone is
    # only correct in the contiguous case, and silently mis-attributes energy
    # and coverage in the other.
    columns: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))

    @property
    def dimensions(self) -> int:
        return self.matrix.shape[1]

    @property
    def nonzeros(self) -> int:
        return int(self.matrix.nnz)

    @property
    def density_percent(self) -> float:
        if self.matrix.shape[0] == 0 or self.matrix.shape[1] == 0:
            return 0.0
        return self.matrix.nnz / (self.matrix.shape[0] * self.matrix.shape[1]) * 100.0


def build_block(
    features: Sequence[Mapping[str, Any]],
    field_name: str,
    field_cfg: FieldConfig,
    config: PreprocessConfig,
) -> BlockResult:
    """Vectorise one field into its own L2-normalised block."""
    texts = _block_texts(features, field_name)
    vectorizer = _vectorizer_for(field_cfg, config)
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        # min_df eliminated the entire vocabulary (e.g. an empty field).
        # Represent the block as a correctly-shaped zero matrix so the
        # representation geometry stays well defined.
        matrix = sp.csr_matrix((len(texts), 0), dtype=np.float32)

    vocabulary = list(vectorizer.get_feature_names_out()) if matrix.shape[1] else []
    if config.per_block_l2 and matrix.shape[1]:
        matrix = normalize_rows(matrix)

    with_tokens = int((matrix.indptr[1:] - matrix.indptr[:-1]).astype(bool).sum()) if matrix.shape[1] else 0
    return BlockResult(
        name=field_name,
        matrix=matrix.tocsr().astype(np.float32),
        vocabulary=vocabulary,
        weight=field_cfg.weight,
        documents_with_tokens=with_tokens,
    )


def normalize_rows(matrix: sp.csr_matrix) -> sp.csr_matrix:
    """L2-normalise each non-empty row, leaving all-zero rows at zero."""
    matrix = matrix.tocsr(copy=True)
    squared = matrix.multiply(matrix)
    norms = np.sqrt(np.asarray(squared.sum(axis=1)).ravel())
    inv = np.zeros_like(norms)
    nonzero = norms > 0
    inv[nonzero] = 1.0 / norms[nonzero]
    return sp.diags(inv).dot(matrix).tocsr()


@dataclass
class RepresentationResult:
    spec: RepresentationSpec
    blocks: list[BlockResult]
    matrix: sp.csr_matrix
    feature_names: list[str]

    # -- descriptive statistics ------------------------------------------
    @property
    def n_documents(self) -> int:
        return self.matrix.shape[0]

    @property
    def dimensions(self) -> int:
        return self.matrix.shape[1]

    @property
    def nonzeros(self) -> int:
        return int(self.matrix.nnz)

    @property
    def density_percent(self) -> float:
        if self.n_documents == 0 or self.dimensions == 0:
            return 0.0
        return self.nonzeros / (self.n_documents * self.dimensions) * 100.0

    @property
    def all_zero_documents(self) -> int:
        if self.dimensions == 0:
            return self.n_documents
        per_row = np.diff(self.matrix.indptr)
        return int((per_row == 0).sum())

    def field_contribution_share(self) -> dict[str, float]:
        """
        Share of total squared L2 mass attributable to each field.

        This is the measurement behind the "cast must not dominate"
        requirement: it is the fraction of a vector's energy that a field
        actually controls. Measured on the final matrix, so block weights and
        the final L2 normalisation are both included.
        """
        if self.dimensions == 0:
            return {}
        squared_total = float(np.asarray(self.matrix.power(2).sum()))
        if squared_total <= 0:
            return {}
        shares: dict[str, float] = {}
        for block in self.blocks:
            cols = block.columns
            if cols.size:
                chunk = self.matrix[:, cols]
                block_energy = float(np.asarray(chunk.power(2).sum()))
            else:
                block_energy = 0.0
            shares[block.name] = round(block_energy / squared_total * 100.0, 2)
        return shares

    def per_document_coverage(self) -> dict[str, Any]:
        """How many films carry at least one feature from each field."""
        out: dict[str, Any] = {}
        for block in self.blocks:
            cols = block.columns
            if cols.size:
                per_row = np.diff(self.matrix[:, cols].tocsr().indptr)
                out[block.name] = int((per_row > 0).sum())
            else:
                out[block.name] = 0
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.spec.key,
            "label": self.spec.label,
            "fields": list(self.spec.fields),
            "documents": self.n_documents,
            "dimensions": self.dimensions,
            "nonzeros": self.nonzeros,
            "density_percent": round(self.density_percent, 4),
            "mean_nonzeros_per_document": round(
                self.nonzeros / self.n_documents, 2
            )
            if self.n_documents
            else 0.0,
            "all_zero_documents": self.all_zero_documents,
            "blocks": [
                {
                    "field": b.name,
                    "dimensions": b.dimensions,
                    "nonzeros": b.nonzeros,
                    "density_percent": round(b.density_percent, 4),
                    "documents_with_tokens": b.documents_with_tokens,
                    "weight": b.weight,
                }
                for b in self.blocks
            ],
            "field_contribution_share_percent": self.field_contribution_share(),
            "documents_with_field": self.per_document_coverage(),
        }


def _flat_vectorizer(config: PreprocessConfig, selected: dict[str, FieldConfig]) -> TfidfVectorizer:
    """
    One vectoriser over the concatenated, namespaced document.

    Because every token already carries its namespace prefix, a single
    vocabulary is still field-isolated; what changes is that a field's share
    of the vector follows its share of tokens rather than being forced equal.

    A single ``min_df`` must serve every field, so the *strictest* (maximum)
    per-field value is used. Taking the minimum instead would let a field
    configured with min_df=1 (genres) admit singletons for the whole
    vocabulary. With this dataset the strictest value is 2 and all 19 genres
    appear in 96+ films, so no genre is lost.
    """
    strictest = max((cfg.min_df for cfg in selected.values()), default=2)
    return TfidfVectorizer(
        analyzer="word",
        token_pattern=r"(?u)\S+",
        lowercase=False,
        min_df=strictest,
        sublinear_tf=config.sublinear_tf,
        norm="l2",
        stop_words=None,      # per-field stopword policy applied upstream
        smooth_idf=True,
        dtype=np.float32,
    )


def build_flat_representation(
    features: Sequence[Mapping[str, Any]],
    spec: RepresentationSpec,
    config: PreprocessConfig,
) -> RepresentationResult:
    """Vectorise the selected fields as one concatenated document."""
    all_fields = config.fields()
    selected = {n: all_fields[n] for n in spec.fields if all_fields[n].enabled}

    texts = [
        " ".join(
            tok
            for name in spec.fields
            for tok in item["tokens"].get(name, [])
        )
        for item in features
    ]
    vectorizer = _flat_vectorizer(config, selected)
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        matrix = sp.csr_matrix((len(texts), 0), dtype=np.float32)
    vocabulary = list(vectorizer.get_feature_names_out()) if matrix.shape[1] else []
    if config.final_l2 and matrix.shape[1]:
        matrix = normalize_rows(matrix)
    matrix = matrix.tocsr().astype(np.float32)

    blocks = []
    for name, cfg in selected.items():
        cols = _namespace_columns(vocabulary, cfg.namespace)
        blocks.append(
            BlockResult(
                name=name,
                matrix=matrix[:, cols] if cols.size else _empty_like(matrix),
                vocabulary=[],
                weight=cfg.weight,
                documents_with_tokens=sum(1 for item in features if item["tokens"].get(name)),
                columns=cols,
            )
        )
    return RepresentationResult(spec, blocks, matrix, vocabulary)


def _empty_like(matrix: sp.csr_matrix) -> sp.csr_matrix:
    return sp.csr_matrix((matrix.shape[0], 0), dtype=np.float32)


def _namespace_columns(vocabulary: list[str], namespace: str) -> np.ndarray:
    """
    Indices of one namespace's columns.

    ``namespace`` is the configured prefix (``ov``, ``kw``, ``gn``, ``cast``,
    ``dir``), which is not always the field name.
    """
    prefix = f"{namespace}:"
    return np.array(
        [i for i, term in enumerate(vocabulary) if term.startswith(prefix)],
        dtype=np.int64,
    )


def build_representation(
    features: Sequence[Mapping[str, Any]],
    spec: RepresentationSpec,
    config: PreprocessConfig,
) -> RepresentationResult:
    """Vectorise the fields named by ``spec`` according to ``config.block_mode``."""
    if config.block_mode == "flat":
        return build_flat_representation(features, spec, config)
    if config.block_mode == "per_block":
        return build_blockwise_representation(features, spec, config)
    raise ValueError(
        f"unknown block_mode {config.block_mode!r}; expected 'per_block' or 'flat'"
    )


def compare_all(
    features: Sequence[Mapping[str, Any]],
    config: PreprocessConfig,
    specs: Sequence[RepresentationSpec] = REPRESENTATIONS,
) -> list[RepresentationResult]:
    """Build every candidate under one config. Returns results in spec order."""
    return [build_representation(features, spec, config) for spec in specs]


def build_blockwise_representation(
    features: Sequence[Mapping[str, Any]],
    spec: RepresentationSpec,
    config: PreprocessConfig,
) -> RepresentationResult:
    """Vectorise the fields named by ``spec`` and concatenate the blocks."""
    all_fields = config.fields()
    blocks: list[BlockResult] = []
    for field_name in spec.fields:
        field_cfg = all_fields[field_name]
        if not field_cfg.enabled:
            continue
        blocks.append(build_block(features, field_name, field_cfg, config))

    if not blocks:
        empty = sp.csr_matrix((len(features), 0), dtype=np.float32)
        return RepresentationResult(spec, [], empty, [])

    parts = []
    for block in blocks:
        if block.dimensions == 0:
            continue
        if block.weight != 1.0:
            parts.append(block.matrix * np.float32(block.weight))
        else:
            parts.append(block.matrix)
    matrix = sp.hstack(parts, format="csr") if parts else sp.csr_matrix(
        (len(features), 0), dtype=np.float32
    )

    if config.final_l2:
        matrix = normalize_rows(matrix)

    # Contiguous by construction, but recorded explicitly so every consumer
    # uses the same source of truth for "which columns are this field's".
    offset = 0
    for block in blocks:
        block.columns = np.arange(offset, offset + block.dimensions, dtype=np.int64)
        offset += block.dimensions

    names: list[str] = []
    for block in blocks:
        names.extend(block.vocabulary)
    return RepresentationResult(spec, blocks, matrix.tocsr().astype(np.float32), names)
