"""
Stage the built frontend into ``public/`` for a Vercel build.

The FastAPI framework preset does not read ``outputDirectory``. Vercel serves
static assets for a backend preset from a ``public/`` directory at the project
root, and that is what ``.vercel/output/static`` is populated from. ``vite build``
writes to ``frontend/dist``, so without this step the build succeeds and the
deployment has no frontend at all: every route falls through to the Python
function, which 404s on ``/``.

``public/`` is build output, not a source directory. It is gitignored, it is
not in version control, and this script replaces it wholesale on every run so
that hashed assets from an earlier build cannot linger and be served.

Standard library only, for the same reason as ``fetch_corpus.py``: this runs on
Vercel's builder and a build step must not add a dependency.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

#: Where ``vite build`` writes. Matches ``frontend/vite.config.ts``.
DEFAULT_SOURCE = Path("frontend/dist")

#: Where the FastAPI preset expects static assets. Vercel serves ``public/logo.svg``
#: at ``/logo.svg``, so the site root must land at the top of this directory.
DEFAULT_TARGET = Path("public")

#: A Vite build always emits this. Its absence means the JS step did not run, and
#: staging the directory anyway would ship a site that 404s on ``/``.
ENTRYPOINT = "index.html"


def _fail(message: str) -> int:
    print(f"stage_frontend: {message}", file=sys.stderr)
    return 1


def _relative_size(root: Path) -> tuple[int, int]:
    """Return ``(file_count, total_bytes)`` for the regular files under ``root``."""
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"vite build output (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"static directory served by Vercel (default: {DEFAULT_TARGET})",
    )
    args = parser.parse_args(argv)

    source = args.source.resolve()
    target = args.target.resolve()

    if not source.is_dir():
        return _fail(
            f"{args.source} does not exist. The build command must run the frontend "
            "build before this script."
        )
    if not (source / ENTRYPOINT).is_file():
        return _fail(
            f"{args.source}/{ENTRYPOINT} is missing, so {args.source} is not a "
            "completed frontend build. Refusing to stage a site that would 404 on /."
        )
    if target == source or source in target.parents:
        return _fail(
            f"--target ({args.target}) must be outside --source ({args.source}), or "
            "this would delete the build output it is copying."
        )

    # Replace wholesale. Vite emits content-hashed filenames, so a partial
    # clean leaves last build's index-*.js next to this one's and the old bundle
    # stays reachable at its URL forever.
    if target.exists():
        shutil.rmtree(target)

    count, total_bytes = _relative_size(source)
    shutil.copytree(source, target)

    staged_count, staged_bytes = _relative_size(target)
    if (staged_count, staged_bytes) != (count, total_bytes):
        return _fail(
            f"staged {staged_count} files/{staged_bytes} bytes into {args.target}, but "
            f"{args.source} holds {count} files/{total_bytes} bytes."
        )

    print(
        f"stage_frontend: ok -- {staged_count} files, {staged_bytes} bytes "
        f"{args.source} -> {args.target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())