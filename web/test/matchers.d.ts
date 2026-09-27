// Tells TypeScript about the `jest-axe` matcher `test/setup.ts` adds to Vitest's `expect`.
// (`@types/jest-axe` augments Jest's own matcher namespace, not Vitest's.)
import 'vitest'

interface AxeMatchers<R = unknown> {
  toHaveNoViolations: () => R
}

declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- module augmentation
  interface Assertion<T = unknown> extends AxeMatchers<T> {}
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- module augmentation
  interface AsymmetricMatchersContaining extends AxeMatchers {}
}
