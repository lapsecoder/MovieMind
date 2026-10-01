"""
Materialise the processed corpus for a Vercel build.

**This is the only thing standing between the corpus and the public repository,
so it fails closed.** Three modes, in priority order:

1. ``MOVIEMIND_CORPUS_URL`` is set -- download from it. This is the production
   path. The URL is expected to be non-public, and ``MOVIEMIND_CORPUS_TOKEN``,
   if set, is sent as a bearer token. Nothing is written until the whole
   response has been received, validated and (when ``MOVIEMIND_CORPUS_SHA256``
   is set) checksummed, so a truncated download cannot produce a half-written
   corpus that fails later at request time.
2. ``MOVIEMIND_CORPUS_URL`` is unset and a corpus already exists at the
   destination -- validate it in place. This is what makes ``vercel build`` and
   ``vercel dev`` work on a laptop with no configuration.
3. Neither -- exit non-zero with the exact command to run.

The destination defaults to ``data/processed/movies.jsonl``, which is already
the default of ``Settings.corpus_path``. The deployed function therefore needs
no corpus-path environment variable at all, and no application code changed.

Standard library only. This runs on Vercel's builder before Node is invoked,
and adding a dependency to satisfy a build step would be a poor trade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: Where the corpus lands. Matches ``Settings.corpus_path``'s default.
DEFAULT_DESTINATION = Path("data/processed/movies.jsonl")

#: Keys a Phase 3 corpus record is known to carry. `tokens` is the discriminator
#: that matters: the raw TMDb snapshot has `overview`, `cast` and `crew` instead,
#: so a build that accidentally points at `data/raw/` fails here instead of
#: producing a function that starts and then 503s. Checked on the first and last
#: line only -- a full pass over 5,000 records to re-derive what `load_corpus`
#: already validates would be redundant, and the point here is to catch "this URL
#: returned an HTML error page" cheaply.
REQUIRED_FIELDS = ("id", "title", "tokens")

#: Refuse anything implausible for a 5,000-film snapshot. A login page or an API
#: error body is a few hundred bytes; a truncated corpus is not.
MIN_BYTES = 64 * 1024

_CHUNK = 256 * 1024


def _fail(message: str) -> int:
    print(f"fetch_corpus: {message}", file=sys.stderr)
    return 1


def _first_and_last_line(path: Path) -> tuple[str, str]:
    """Return the first and last non-empty line of a JSONL file."""
    first = ""
    last = ""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if not first:
                first = stripped
            last = stripped
    return first, last


def _validate(path: Path) -> tuple[int, str]:
    """
    Check that ``path`` is a plausible Phase 3 corpus.

    Returns ``(record_count, sha256)``. Raises ``ValueError`` with a reason a
    human can act on, which the caller turns into a non-zero exit.
    """
    size = path.stat().st_size
    if size < MIN_BYTES:
        raise ValueError(
            f"{path} is {size} bytes, which is far too small to be the corpus. "
            "A private URL that returned an error page, or a wrong path, is the "
            "usual cause."
        )

    first_line, last_line = _first_and_last_line(path)
    if not first_line:
        raise ValueError(f"{path} contains no records")

    for label, line in (("first", first_line), ("last", last_line)):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"the {label} line of {path} is not valid JSON ({exc}). "
                "The response is probably not the corpus."
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(f"the {label} line of {path} is not a JSON object")
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise ValueError(
                f"the {label} line of {path} is missing {', '.join(missing)}. "
                "This does not look like a Phase 3 corpus (run "
                "scripts/build_features.py to produce one)."
            )

    count = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
            count += chunk.count(b"\n")
    if count == 0:
        raise ValueError(f"{path} has content but no line terminators")
    return count, digest.hexdigest()


def _download(url: str, token: str | None, destination: Path) -> None:
    """Stream ``url`` to ``destination`` via a temporary file, then swap it in."""
    request = urllib.request.Request(url, headers={"User-Agent": "MovieMind-deploy"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            temporary.open("wb") as handle,
        ):
            while chunk := response.read(_CHUNK):
                handle.write(chunk)
    except urllib.error.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        if exc.code in (401, 403, 404):
            raise ValueError(
                f"{url} returned HTTP {exc.code}. The artifact must be reachable "
                "without being public, so the token and its scopes are the first "
                "things to check."
            ) from exc
        raise ValueError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"could not reach {url}: {exc.reason}") from exc

    # os.replace is atomic within a filesystem, so a reader never observes a
    # partially written corpus at `destination`.
    os.replace(temporary, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("MOVIEMIND_CORPUS_PATH", DEFAULT_DESTINATION)),
        help="where the corpus is written (default: $MOVIEMIND_CORPUS_PATH or "
        f"{DEFAULT_DESTINATION})",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("MOVIEMIND_CORPUS_URL"),
        help="private artifact URL (default: $MOVIEMIND_CORPUS_URL)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("MOVIEMIND_CORPUS_TOKEN"),
        help="bearer token for --url (default: $MOVIEMIND_CORPUS_TOKEN)",
    )
    parser.add_argument(
        "--expect-sha256",
        default=os.environ.get("MOVIEMIND_CORPUS_SHA256"),
        help="fail unless the corpus hashes to this (default: "
        "$MOVIEMIND_CORPUS_SHA256)",
    )
    args = parser.parse_args(argv)

    if args.url:
        print(f"fetch_corpus: downloading {args.url} -> {args.out}")
        try:
            _download(args.url, args.token, args.out)
        except ValueError as exc:
            return _fail(str(exc))
    elif not args.out.exists():
        return _fail(
            f"neither MOVIEMIND_CORPUS_URL nor an existing corpus at {args.out}. "
            "Build the corpus locally with scripts/build_features.py, or set "
            "MOVIEMIND_CORPUS_URL to a private artifact."
        )
    else:
        print(f"fetch_corpus: no URL configured, validating existing {args.out}")

    try:
        count, digest = _validate(args.out)
    except (OSError, ValueError) as exc:
        return _fail(str(exc))

    if args.expect_sha256 and digest.lower() != args.expect_sha256.strip().lower():
        return _fail(
            f"{args.out} hashes to {digest}, expected {args.expect_sha256}. "
            "The artifact and MOVIEMIND_CORPUS_SHA256 disagree; refusing to build."
        )

    print(f"fetch_corpus: ok -- {count} records, sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
