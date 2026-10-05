// =============================================================================
// vite.config.ts — dev server, production build and Vitest configuration
// =============================================================================
// Purpose:
//   One file for the bundler and the test runner (Vitest reads its `test` field), so there is
//   a single source for how the app is built and how it is tested.
// Design:
//   The coverage gate requires 85% of lines and 80% of branches.
//   The bootstrap files (src/main.tsx, src/console-main.tsx) are excluded: each only wires
//   `createRoot`/`render`. Two HTML entries (index.html, console.html) make this a multi-page
//   build (Vite's own documented pattern, no router library): the customer chat and the agent
//   console are separate demo paths, served as separate static pages by the same unmodified
//   nginx image (web/Dockerfile has no SPA rewrite rule, so a pathname-branch inside one App
//   would need one) — the dev server serves both HTML entries without this `input` map; only
//   `vite build` needs it to emit `console.html` into `dist/`.
// =============================================================================

/// <reference types="vitest/config" />
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        console: resolve(import.meta.dirname, 'console.html'),
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./test/setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      // The bootstrap files: each only wires `createRoot`/`render` and has no branch worth a test.
      exclude: ['src/main.tsx', 'src/console-main.tsx'],
      thresholds: { lines: 85, branches: 80 },
    },
  },
})
