// =============================================================================
// vite.config.ts — dev server, production build and Vitest configuration
// =============================================================================
// Purpose:
//   One file for the bundler and the test runner (Vitest reads its `test` field), so there is
//   a single source for how the app is built and how it is tested.
// Design:
//   The coverage gate matches the Python side's floor (pyproject.toml): 85% lines, 80% branches.
//   The bootstrap file (src/main.tsx) is excluded: it only wires `createRoot`/`render`.
// =============================================================================

/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./test/setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      // The bootstrap file: it only wires `createRoot`/`render` and has no branch worth a test.
      exclude: ['src/main.tsx'],
      // The same combined line+branch floor as the Python side (pyproject.toml).
      thresholds: { lines: 85, branches: 80 },
    },
  },
})
