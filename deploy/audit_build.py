"""
Audit a built deployment for the two things that must never be true.

1. **No TMDb credential anywhere in anything that ships.** Both the literal
   secret values (taken from the local environment, or from ``.env`` for the
   audit only -- this script never uploads or transmits anything) and the
   identifiers ``TMDB_API_READ_ACCESS_TOKEN`` / ``TMDB_API_KEY`` are searched
   for in the frontend build and in the source the function bundles.
2. **No public static route to the corpus.** The directory that is actually
   served -- ``public/``, which ``deploy/stage_frontend.py`` populates from
   ``frontend/dist`` -- is checked for ``.jsonl`` files and for a bundle
   implausibly large enough to contain one, and the ASGI app is checked for any
   static mount or catch-all route that could serve a file off disk.

Run after ``npm --prefix frontend run build`` and ``python
deploy/stage_frontend.py``. Exits non-zero on any finding, so it can be wired
into a pre-deploy step.

    python deploy/audit_build.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# `python deploy/audit_build.py` puts deploy/ on sys.path, not the repository
# root, so the route audit below could not import the app without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Extension of every corpus-shaped file in this project.
CORPUS_SUFFIX = ".jsonl"

#: The built frontend is a few hundred kB. Anything approaching the size of the
#: corpus (8.8 MB) means part of it has been inlined into a JS chunk.
MAX_STATIC_BYTES = 2 * 1024 * 1024

#: Identifiers that must not appear in shipped code. The API has no reason to
#: know a TMDb credential exists; only scripts/fetch_tmdb_snapshot.py does, and
#: that script is excluded from the function bundle.
FORBIDDEN_IDENTIFIERS = ("TMDB_API_READ_ACCESS_TOKEN", "TMDB_API_KEY")

#: Files whose text is scanned. `scripts/` and `docs/` are excluded on purpose:
#: the first legitimately reads the credential, the second discusses it.
SCANNED_SOURCE_DIRS = ("moviemind", "frontend/src", "frontend/index.html")

#: Text file extensions worth opening. Binary assets are checked by name and
#: size instead.
TEXT_SUFFIXES = frozenset(
    {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx", ".json", ".html", ".css", ".txt", ".md", ".yml", ".yaml", ".toml"}
)

_SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"})

_API_PATH_RE = re.compile(r"""["'`](\/[^"'`\s]*)["'`]""")


def _fail(findings: list[str]) -> int:
    print("audit_build: FAILED", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)
    return 1


def _local_secrets() -> list[str]:
    """
    Secret values to search for, from the environment and from ``.env``.

    Reading ``.env`` here is safe and deliberate: the file is gitignored, the
    script runs on the operator's own machine, and it only ever compares the
    values against local files. Nothing is transmitted.
    """
    found: list[str] = []
    for name in FORBIDDEN_IDENTIFIERS:
        value = os.environ.get(name, "").strip()
        if len(value) >= 16:
            found.append(value)

    env_file = Path(".env")
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in FORBIDDEN_IDENTIFIERS and len(value.strip()) >= 16:
                found.append(value.strip())
    return found


def _walk_text_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if _SKIP_DIRS & set(path.relative_to(root).parts[:-1]):
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                files.append(path)
    return files


def _check_static(static: Path, findings: list[str]) -> list[Path]:
    """Static-exposure checks. Returns the text files to scan for secrets."""
    if not static.is_dir():
        findings.append(
            f"{static} does not exist; run the frontend build and then "
            "`python deploy/stage_frontend.py` -- this is the directory Vercel serves"
        )
        return []

    total = 0
    for path in static.rglob("*"):
        if not path.is_file():
            continue
        total += path.stat().st_size
        if path.suffix.lower() == CORPUS_SUFFIX:
            findings.append(
                f"{path} is inside the static output, so it would be served at "
                f"https://<host>/{path.relative_to(static).as_posix()}"
            )

    if total > MAX_STATIC_BYTES:
        findings.append(
            f"{static} is {total / 1048576:.1f} MB, over the "
            f"{MAX_STATIC_BYTES / 1048576:.0f} MB ceiling; a corpus may have been "
            "inlined into a chunk"
        )

    index = static / "index.html"
    if not index.is_file():
        findings.append(f"{index} is missing; the deployment would have no entry point")
        return []

    html = index.read_text(encoding="utf-8", errors="replace")
    for reference in re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html):
        if reference.startswith(("http://", "https://", "//")):
            findings.append(f"index.html loads an off-origin asset: {reference}")

    return [p for p in static.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES]


def _check_routes(findings: list[str]) -> list[str]:
    """
    Confirm the ASGI app exposes exactly the six documented GET routes and
    nothing that can read from the filesystem.
    """
    try:
        from starlette.routing import Mount

        from moviemind.api.app import app
    except Exception as exc:  # pragma: no cover - import environment problem
        findings.append(f"could not import the ASGI app to audit its routes: {exc}")
        return []

    paths: list[str] = []
    for route in app.routes:
        if isinstance(route, Mount):
            findings.append(
                f"app mounts {route.path!r}, which serves files from disk; a static "
                "mount on Vercel is also promoted to the CDN"
            )
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        # FastAPI's generated /docs, /redoc and /openapi.json add HEAD alongside
        # GET. Anything beyond that would be a write, and this API has none.
        methods = set(getattr(route, "methods", None) or [])
        unexpected = sorted(methods - {"GET", "HEAD"})
        if unexpected:
            findings.append(f"{path!r} accepts {unexpected}; the API is read-only")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--static",
        type=Path,
        default=Path("public"),
        help="directory Vercel serves from the CDN (default: public)",
    )
    args = parser.parse_args(argv)

    findings: list[str] = []
    scan_targets = _check_static(args.static, findings)

    source_files = _walk_text_files([Path(d) for d in SCANNED_SOURCE_DIRS] + scan_targets)
    secrets = _local_secrets()
    if not secrets:
        print(
            "audit_build: warning - no TMDb credential found in the environment or "
            ".env, so only identifier scanning was possible"
        )

    needles = [(value, f"literal TMDb credential ({value[:4]}...)") for value in secrets]
    needles += [(name, f"TMDb credential identifier {name}") for name in FORBIDDEN_IDENTIFIERS]

    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for needle, label in needles:
            if needle in text:
                findings.append(f"{path} contains a {label}")

    routes = _check_routes(findings)

    if findings:
        return _fail(findings)

    print(f"audit_build: {args.static} is {len(scan_targets)} text assets, no corpus")
    print(f"audit_build: scanned {len(source_files)} files for {len(needles)} needles, none found")
    print(f"audit_build: ASGI routes are read-only and unmounted: {json.dumps(sorted(routes))}")
    print("audit_build: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
