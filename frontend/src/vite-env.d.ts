/// <reference types="vite/client" />

/**
 * Ambient declarations for Vite.
 *
 * This file is what makes `import.meta.env.VITE_API_BASE_URL` type-check and
 * what tells TypeScript that `import './index.css'` is a valid side-effect
 * import. Without it, `import.meta.env` is an error and the CSS import in
 * `main.tsx` is "cannot find module".
 *
 * `ImportMetaEnv` is extended with the one variable this app reads, so a typo
 * in the variable name is a compile error rather than an `undefined` that
 * silently falls back to the default at runtime. That matters here: a mistyped
 * `VITE_API_BAS_URL` would otherwise produce an app that quietly talks to its
 * own origin and fails with 404s that look like a proxy problem.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
