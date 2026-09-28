import type { ReactNode } from 'react'

import { FALLBACK_TMDB_NOTICE, type AboutData } from '../hooks/useAbout'
import { cx } from '../lib/format'

/**
 * Footer attribution, and the app's own limits.
 *
 * The TMDb notice is rendered **verbatim** and is not styled, reworded, or
 * truncated. `docs/phase-05-api.md` sec 8 pins the exact sentence and
 * `test_tmdb_notice_is_verbatim` in the backend suite asserts it; the same
 * wording is asserted here against the fixture, so a careless edit to this file
 * fails a front-end test rather than shipping a licence violation.
 *
 * It is fetched from `GET /meta` so the text travels with the backend and cannot
 * drift from it, falling back to an identical built-in constant only when the
 * API is unreachable. Attribution must be visible even in that degraded case,
 * which is why the fallback exists rather than the notice simply being omitted.
 */
export function AboutPanel({ about }: { about: AboutData }): ReactNode {
  const { meta, usedFallback } = about
  const notice = meta?.attribution.notice ?? FALLBACK_TMDB_NOTICE

  return (
    <section
      aria-labelledby="about-heading"
      className="rounded-xl border border-line bg-surface"
    >
      <h2
        id="about-heading"
        className="border-b border-line px-4 py-3 text-sm font-semibold text-ink"
      >
        About this tool
      </h2>
      <div className="space-y-4 p-4 text-xs text-ink-muted">
        <p>
          MovieMind finds films that read similarly. It compares the indexed text
          of one film against the rest of the catalogue and returns the nearest
          matches by cosine similarity.
        </p>

        {/*
         * Licence obligations. Kept together and unstyled so it reads as
         * required attribution rather than as branding.
         */}
        <div className="space-y-2 border-t border-line pt-3">
          <p data-testid="tmdb-notice" className="text-ink-muted">
            {notice}
          </p>
          {meta ? (
            <p className="text-ink-faint">
              {meta.attribution.source} · {meta.attribution.license}
            </p>
          ) : null}
          {usedFallback ? (
            <p
              className="text-ink-faint"
              data-testid="about-fallback"
              role="status"
            >
              Live configuration could not be loaded, so the required notice is
              shown from a built-in copy.
            </p>
          ) : null}
        </div>

        {meta ? (
          <div className="space-y-2 border-t border-line pt-3">
            <p className="text-ink-faint">
              Similarity is {meta.similarity}. A match must share at least{' '}
              {meta.min_shared_terms} indexed term
              {meta.min_shared_terms === 1 ? '' : 's'}.
            </p>
            {/*
             * The engine's own caveats, shown rather than hidden. These are the
             * reasons its output should not be read as a quality ranking, and
             * burying them would be the dishonest choice.
             */}
            {meta.notes.length > 0 ? (
              <ul className="list-disc space-y-1 pl-4 text-ink-faint">
                {meta.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
            <p className="font-mono text-ink-faint">
              API {meta.api_version} · config {meta.config_fingerprint.slice(0, 12)}
            </p>
          </div>
        ) : null}
      </div>
    </section>
  )
}

/**
 * A short status pill for the catalogue, from `/ready`.
 *
 * Kept in the header rather than the About panel because it answers "is the
 * thing working right now?", which is a live question, not a description.
 */
export function StatusPill({
  label,
  tone,
}: {
  label: string
  tone: 'ready' | 'loading' | 'error'
}): ReactNode {
  return (
    <span
      data-testid={`status-${tone}`}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
        tone === 'ready' && 'border-success/30 text-success',
        tone === 'loading' && 'border-line-strong text-ink-muted',
        tone === 'error' && 'border-danger/40 text-danger',
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          'h-1.5 w-1.5 rounded-full',
          tone === 'ready' && 'bg-success',
          tone === 'loading' && 'bg-ink-faint',
          tone === 'error' && 'bg-danger',
        )}
      />
      {label}
    </span>
  )
}
