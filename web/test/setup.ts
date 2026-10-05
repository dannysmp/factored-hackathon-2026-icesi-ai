/**
 * Vitest setup, loaded once before every test file (`vite.config.ts`'s `setupFiles`): the
 * jest-axe matcher and Testing Library's teardown between tests.
 */
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { toHaveNoViolations } from 'jest-axe'
import { afterEach, beforeEach, expect, vi } from 'vitest'

// Every test can assert `expect(container).toHaveNoViolations()` without importing it itself.
expect.extend(toHaveNoViolations)

// jsdom does not implement scrolling, so a component that scrolls the newest message into view
// would throw; tests that care about it assert on this spy.
Element.prototype.scrollIntoView = vi.fn()

// jsdom reports `en-US`; the interface starts from the browser's language, so every test begins
// from a Spanish browser (the product's first language) unless it sets its own.
beforeEach(() => {
  vi.spyOn(window.navigator, 'language', 'get').mockReturnValue('es-CO')
})

// `globals: false` (vite.config.ts) means Testing Library can't auto-detect a global `afterEach`
// to unmount the previous test's render; without this, two tests in one file both leave a
// `<main>` in `document.body`, and axe correctly reports "more than one main landmark".
afterEach(() => {
  cleanup()
})
