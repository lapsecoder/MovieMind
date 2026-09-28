import { useEffect, useState } from 'react'

/**
 * Debounce a rapidly-changing value.
 *
 * Returns the value that has been stable for `delayMs`. The input state updates
 * immediately so the text field stays responsive; only the *search* is delayed.
 *
 * The pending timer is cleared on unmount and whenever the input changes, which
 * is what stops a queued value from firing after the user has moved on.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    if (value === debounced) return
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs, debounced])

  return debounced
}
