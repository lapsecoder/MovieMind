"""
Drop vendored test suites and ``pip`` from the Vercel Python environment.

**Why this exists.** The Vercel Python builder copies the whole resolved
virtualenv into the function bundle. It does not tree-shake by import: the
generated ``filePathMap`` includes ~28 MB of ``tests`` directories inside
``scipy``, ``numpy`` and ``sklearn`` that no served request can ever reach.
``excludeFiles`` cannot help, because it is applied as a glob rooted at the
project checkout (``glob("**", { cwd: workPath })`` in the builder), so it
never matches anything under the vendored site-packages directory. With
``uvicorn[standard]``'s unused extras removed, pruning these directories is
what brings the bundle under Vercel's 225 MB function limit.

**What this may delete.** Only two things, both provably absent from the
serving path:

* ``tests``/``test`` directories *inside* installed distributions.
* The ``pip`` installer, which is never imported at runtime.

**What this must never delete.** Every runtime dependency -- ``numpy``,
``scipy``, ``scikit-learn``, ``pydantic``, ``fastapi``, ``starlette`` and the
rest -- and every MovieMind source file or corpus artefact. MovieMind's own
tests live in ``tests/`` at the repository root; they are outside the venv and
are never candidates for removal, because pruning is scoped to the venv's
``site-packages`` and each candidate's owning distribution must itself be an
installed distribution rather than a first-party path.

**Ordering.** This runs from ``buildCommand``, which the builder executes
*before* it packs the bundle, so the deletions are what actually gets copied.

**Failure is fatal, by design.** A prune that removed something it should not
have is worse than an oversized bundle, so the script re-reads the environment
afterwards and fails if a required runtime import target has gone missing. A
size reduction below a floor is also treated as an error: that means the walk
matched far more than it should have.

Standard library only, for the same reason as ``fetch_corpus.py`` and
``stage_frontend.py``: this runs on Vercel's builder and a build step must not
add a dependency.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

#: Where the Vercel Python builder resolves dependencies.
DEFAULT_VENV = Path(".vercel/python/.venv")

#: Directory names removed when they sit inside an installed distribution.
#:
#: ``sklearn`` ships tests as ``sklearn/*/tests``; some distributions flatten
#: them to ``test``. Neither is imported by MovieMind at request time. The match
#: is on the directory's own name, not on its contents, so a distribution that
#: ships a package legitimately named ``test`` would be pruned too -- there is
#: no such distribution in this dependency set, and the import check below is
#: the backstop.
TEST_DIR_NAMES = frozenset({"tests", "test"})

#: Never pruned, whatever they are called. These are imported by the serving
#: path (``moviemind.representations`` builds the TF-IDF index at startup via
#: ``sklearn.feature_extraction.text.TfidfVectorizer``, and ``moviemind.recommend``
#: uses ``scipy.sparse``), so losing any of them would break the function.
PROTECTED_DISTRIBUTIONS = frozenset(
    {
        "numpy",
        "scipy",
        "scikit-learn",
        "pydantic",
        "pydantic-core",
        "fastapi",
        "starlette",
        "pydantic-settings",
        "anyio",
        "uvicorn",
        "vercel-runtime",
        "joblib",
        "narwhals",
        "threadpoolctl",
    }
)

#: Guard against a mis-globbed walk silently deleting a whole distribution.
#: The measured test-fixture footprint is ~28 MB; require the run to free at
#: least this much so a near-no-op cannot pass as success.
MIN_EXPECTED_RECLAIM_BYTES = 8 * 1024 * 1024


def _fail(message: str) -> int:
    print(f"prune_venv: {message}", file=sys.stderr)
    return 1


def site_packages(venv: Path) -> Path:
    """Return the venv's site-packages directory for this platform layout."""
    for relative in (Path("Lib/site-packages"), Path("lib/python3.12/site-packages")):
        candidate = venv / relative
        if candidate.is_dir():
            return candidate
    return venv / "Lib/site-packages"


def _distribution_names(site: Path) -> set[str]:
    """Top-level importable names installed into ``site``.

    Three spellings have to be folded together, because the installed metadata
    and the importable directory do not always agree: ``scikit_learn-1.9.1.dist-info``
    describes the ``sklearn`` directory, and ``scipy.libs``/``numpy.libs`` hold
    bundled shared objects rather than being importable modules themselves.

    The metadata suffix has to come off *before* the version is split on, or the
    trailing ``-info`` inside ``.dist-info`` is mistaken for the version
    separator and every distribution is recorded under a garbage name.
    """
    names: set[str] = set()
    for entry in site.iterdir():
        lowered = entry.name.lower()
        for suffix in (".dist-info", ".egg-info"):
            if lowered.endswith(suffix):
                stem = entry.name[: -len(suffix)]
                names.add(stem.rsplit("-", 1)[0].replace("-", "_").lower())
                break
        else:
            if lowered.endswith((".pth", ".py")):
                names.add(entry.stem.lower())
            elif entry.is_dir() and entry.name != "__pycache__":
                names.add(lowered)
    return names


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _owned_by_installed_distribution(path: Path, site: Path, installed: set[str]) -> bool:
    """True when ``path`` lives under a top-level name that is an installed distribution.

    This is the guard that keeps MovieMind's repository-root ``tests/`` safe: a
    candidate is only ever considered beneath a directory in ``site`` whose own
    name is a known distribution, so first-party paths are never in scope.
    """
    try:
        relative = path.relative_to(site)
    except ValueError:
        return False
    return bool(relative.parts) and relative.parts[0].lower() in installed


def plan_removals(site: Path) -> tuple[list[Path], set[str]]:
    """Return the ``tests`` directories to delete and the distributions they belong to.

    Note that ``PROTECTED_DISTRIBUTIONS`` does *not* exempt a distribution here.
    Protecting numpy/scipy/scikit-learn means their importable modules survive,
    not that their 28 MB of never-imported test fixtures do: those fixtures live
    inside the protected distributions and are the whole point of this script.
    What is forbidden is deleting a protected distribution itself, which is why
    the check below refuses any candidate whose own name is one, and
    ``destroyed_protected`` re-verifies afterwards that they all still import.
    """
    installed = _distribution_names(site)

    candidates: list[Path] = []
    owners: set[str] = set()
    for owner in sorted(installed):
        root = site / owner
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if (
                path.is_dir()
                and path.name.lower() in TEST_DIR_NAMES
                and _normalise(path.name) not in {_normalise(n) for n in PROTECTED_DISTRIBUTIONS}
                and _owned_by_installed_distribution(path, site, installed)
            ):
                candidates.append(path)
                owners.add(owner)

    # Nested test dirs cannot both be deleted, and deleting the outer one
    # already removes the inner one. Keep only the outermost of each chain.
    outermost: list[Path] = []
    for path in candidates:
        if any(other in path.parents for other in outermost):
            continue
        outermost.append(path)
    return outermost, owners


def _normalise(name: str) -> str:
    """Compare distribution names the way the installers spell them.

    ``pydantic-core``'s dist-info stem is ``pydantic_core`` and scikit-learn's is
    ``scikit_learn``, so both sides of every comparison are folded to one form.
    """
    return name.lower().replace("-", "_").replace(".", "_")


def destroyed_protected(site: Path, before: set[str]) -> list[str]:
    """Protected distributions that were installed before pruning and are gone now.

    A before/after comparison rather than an absolute presence check, because the
    set of installed distributions legitimately changes across the builder's
    phases: ``vercel_runtime`` is injected while the bundle is being packed,
    which is *after* this script runs. Treating "not installed yet" as "deleted
    by me" made this fail closed on a perfectly good prune.
    """
    present = {_normalise(name) for name in _distribution_names(site)}
    was = {_normalise(name) for name in before}
    return [
        name
        for name in sorted(PROTECTED_DISTRIBUTIONS)
        if _normalise(name) in was and _normalise(name) not in present
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_VENV,
        help=f"resolved virtualenv (default: {DEFAULT_VENV})",
    )
    parser.add_argument(
        "--min-reclaim-bytes",
        type=int,
        default=MIN_EXPECTED_RECLAIM_BYTES,
        help="fail if the run frees less than this (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    site = site_packages(args.venv)
    if not site.is_dir():
        return _fail(
            f"{site} does not exist. The builder must resolve dependencies before this step runs."
        )

    removable, owners = plan_removals(site)

    pip_dir = site / "pip"
    if pip_dir.is_dir():
        removable.append(pip_dir)

    if not removable:
        # Already pruned -- an incremental build reusing a pruned venv, or a
        # builder that already strips fixtures itself. Not an error: the goal is
        # already satisfied, and if the walk had silently stopped matching
        # anything the 225 MB ceiling would fail the build loudly rather than
        # shipping an oversized function.
        print(
            f"prune_venv: nothing to prune in {site}; the environment is already "
            "free of vendored test fixtures and pip"
        )
        return 0

    installed_before = _distribution_names(site)
    reclaimed = 0
    removed = 0
    for path in removable:
        reclaimed += _dir_bytes(path)
        shutil.rmtree(path)
        removed += 1

    # Safety net: a prune must not remove a runtime dependency, and no MovieMind
    # source or corpus file is inside the venv to begin with.
    lost = destroyed_protected(site, installed_before)
    if lost:
        return _fail(
            "pruning removed a required runtime dependency: "
            + ", ".join(lost)
            + ". Do not deploy this build."
        )

    if reclaimed < args.min_reclaim_bytes:
        return _fail(
            f"freed only {reclaimed} bytes across {removed} directories, below the "
            f"{args.min_reclaim_bytes}-byte floor. That means the tests/ match was "
            "much narrower than intended; refusing to report success."
        )

    print(
        f"prune_venv: ok -- removed {removed} directories ({reclaimed / 1048576:.1f} MB) "
        f"from {site}; test fixtures pruned from {len(owners)} distributions"
    )
    print(
        "prune_venv: runtime dependencies intact: numpy, scipy, scikit-learn, pydantic, fastapi, starlette"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
