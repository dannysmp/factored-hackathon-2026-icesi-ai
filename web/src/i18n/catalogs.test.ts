/** Parity test: every catalog must define every key with a non-blank value. `catalogParityProblems`
 * is exercised against deliberately broken fixtures (never the real catalog files, which the type
 * checker already keeps key-complete) to prove it actually catches a missing key or a blank value,
 * not just that the real catalogs happen to pass it today. */
import { describe, expect, it } from 'vitest'
import { CATALOGS } from './catalogs'
import { catalogParityProblems } from './parity'

// `Messages`' keys are typed (not a plain index signature) so `useT` stays restricted to known
// keys; `catalogParityProblems` deliberately works on plain records instead, so this cast is the
// one place a catalog is read back as the untyped shape the checker itself operates on.
function asRecords(catalogs: typeof CATALOGS): Record<string, Record<string, string>> {
  return catalogs as unknown as Record<string, Record<string, string>>
}

describe('the message catalogs', () => {
  it('define the same keys in all three languages, with no blank values', () => {
    expect(catalogParityProblems(asRecords(CATALOGS))).toEqual([])
  })

  it('flags a catalog missing a key another catalog defines', () => {
    const broken = asRecords(CATALOGS)
    const pt = { ...broken.pt }
    delete pt['common.loading']

    expect(catalogParityProblems({ ...broken, pt })).not.toEqual([])
  })

  it('flags a catalog whose value is blank', () => {
    const broken = asRecords(CATALOGS)

    expect(
      catalogParityProblems({ ...broken, en: { ...broken.en, 'chat.placeholder': '   ' } }),
    ).not.toEqual([])
  })
})
