/*
 * `defineConfig` is imported from `vitest/config` rather than `vite`. Vitest
 * augments Vite's config type with the `test` block, and importing from `vite`
 * leaves `test` as an unknown property - a compile error under `tsc -b`. The
 * Vitest entry point re-exports everything Vite's does, so nothing is lost.
 */
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/**
 * Vite config.
 *
 * `VITE_API_BASE_URL` is read by `src/config.ts` via `import.meta.env`, which
 * requires the `VITE_` prefix. It is deliberately *not* read here: keeping the
 * base URL in one module means no component and no test can invent a second one.
 *
 * `server.proxy` exists for one ergonomic reason. In development the browser
 * could call the API directly at `http://127.0.0.1:8000`, but that needs a CORS
 * grant and hard-codes a host into the page. Proxying `/api` through the dev
 * server instead means the frontend uses a same-origin relative path in dev and
 * in tests, and only the deployed build needs an absolute URL.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.BACKEND_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // The suite is fast and pure; a single fork keeps the output readable and
    // avoids the worker-pool noise of parallel runs on a small machine.
    pool: 'threads',
    restoreMocks: true,
  },
})
