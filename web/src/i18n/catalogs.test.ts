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

  it('keep every placeholder in every language, so a translation cannot drop it', () => {
    for (const messages of Object.values(CATALOGS)) {
      expect(messages['chat.charactersLeft']).toContain('{count}')
      expect(messages['chat.caseReference']).toContain('{ticket}')
    }
  })

  it('labels the customer\u2019s own messages in the first person, never as the system addressing them', () => {
    expect(CATALOGS.es['chat.customerLabel']).toBe('Yo:')
    expect(CATALOGS.pt['chat.customerLabel']).toBe('Eu:')
    expect(CATALOGS.en['chat.customerLabel']).toBe('You:')
  })

  it('uses each language\u2019s own words for signing in and for the demonstration identities', () => {
    const portuguese = Object.values(CATALOGS.pt).join(' ')
    const english = Object.values(CATALOGS.en).join(' ')

    expect(portuguese).not.toMatch(/login/i)
    expect(english).not.toMatch(/persona/i)
  })

  it('keeps Spanish in the formal register, with no informal address', () => {
    for (const text of Object.values(CATALOGS.es)) {
      expect(text).not.toMatch(
        /\b(tu|tus|tú|escribe|ingresa|inicia|intenta|inténtalo|revisa|espera)\b/i,
      )
    }
  })

  it('flags a catalog missing a key another catalog defines', () => {
    const broken = asRecords(CATALOGS)
    const pt = { ...broken.pt }
    delete pt['common.retry']

    expect(catalogParityProblems({ ...broken, pt })).not.toEqual([])
  })

  it('flags a catalog whose value is blank', () => {
    const broken = asRecords(CATALOGS)

    expect(
      catalogParityProblems({ ...broken, en: { ...broken.en, 'chat.send': '   ' } }),
    ).not.toEqual([])
  })
})
