// =============================================================================
// eslint.config.js — lint rules (flat config)
// =============================================================================
// Purpose:
//   Type-aware TypeScript rules, React Hooks correctness, accessibility (jsx-a11y, strict
//   preset — the WCAG 2.2 AA target) and Prettier compatibility, at the same strictness as the
//   Python side's ruff + mypy --strict.
// Design:
//   Test files get Node globals (Vitest runs under Node); everything else gets browser globals.
// =============================================================================

import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import { defineConfig, globalIgnores } from 'eslint/config'
import tseslint from 'typescript-eslint'

export default defineConfig([
  globalIgnores(['dist', 'coverage']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactRefresh.configs.vite,
      jsxA11y.flatConfigs.strict,
      prettier,
    ],
    // eslint-plugin-react-hooks@7.1.1's own bundled configs still list `plugins` as an array of
    // names (the pre-flat-config shape), which flat config rejects; its rule map is registered
    // by hand instead of trusting that preset.
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
    },
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'test/**/*.ts'],
    languageOptions: { globals: globals.node },
  },
])
