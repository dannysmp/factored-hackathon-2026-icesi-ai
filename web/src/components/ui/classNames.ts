/** Class-name joining for the shared components, which compose CSS-module classes with a caller's own. */
/**
 * Joins the class names that are present, skipping the ones a condition turned off. Accepts
 * `false` and `undefined` so a conditional class can be written inline as `flag && styles.x`.
 */
export function classNames(...names: readonly (string | false | undefined)[]): string {
  return names.filter((name): name is string => typeof name === 'string' && name !== '').join(' ')
}
