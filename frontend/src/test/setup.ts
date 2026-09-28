import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

/**
 * Global test setup.
 *
 * jsdom does not implement everything React and the app touch, so the gaps are
 * filled once here rather than in each test file.
 */

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

beforeEach(() => {
  // jsdom has no layout engine, so `scrollIntoView` is absent and any call throws.
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }

  // A per-test default so no test can accidentally reach the real network.
  // Individual tests override this with their own handler.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('Unstubbed fetch in test'))),
  )
})
