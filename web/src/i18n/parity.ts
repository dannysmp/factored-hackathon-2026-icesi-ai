/**
 * Checks catalogs as plain records, not through the `Messages` type, so a regression that loosened
 * a catalog's type (e.g. to `Partial<Messages>`) would still be caught at test time, not just by
 * the compiler today.
 */
export function catalogParityProblems(catalogs: Record<string, Record<string, string>>): string[] {
  const allKeys = new Set<string>()
  for (const catalog of Object.values(catalogs)) {
    for (const key of Object.keys(catalog)) {
      allKeys.add(key)
    }
  }

  const problems: string[] = []
  for (const [lang, catalog] of Object.entries(catalogs)) {
    for (const key of allKeys) {
      const value = catalog[key]
      if (value === undefined) {
        problems.push(`${lang} is missing key "${key}"`)
      } else if (value.trim() === '') {
        problems.push(`${lang}'s "${key}" is blank`)
      }
    }
  }
  return problems
}
