/** Joins the class names that are present, skipping the ones a condition turned off. */
export function classNames(...names: readonly (string | false | undefined)[]): string {
  return names.filter((name): name is string => typeof name === 'string' && name !== '').join(' ')
}
