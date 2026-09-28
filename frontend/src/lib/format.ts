/**
 * Small formatting and class helpers.
 *
 * These live outside `primitives.tsx` so that file exports components only. A
 * module that mixes component and non-component exports breaks React Fast
 * Refresh - the dev server can only hot-reload a module whose exports are all
 * components - and it is a warning worth not carrying.
 */

/** Join class names, dropping anything falsy. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/**
 * Turn an API genre code into a display label.
 *
 * Genres arrive lowercase and underscored (`science_fiction`) because they are
 * derived from the Phase 3 token namespace `gn:science_fiction`. The underscore
 * is the API's separator, not part of the word, so it is replaced and the result
 * capitalised for display. Purely a presentation transform: no value is invented
 * and nothing is looked up.
 *
 * Idempotent, so a caller that has already formatted a genre cannot double it.
 */
export function formatGenre(raw: string): string {
  const spaced = raw.replace(/_/g, ' ').trim()
  if (spaced.length === 0) return raw
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
