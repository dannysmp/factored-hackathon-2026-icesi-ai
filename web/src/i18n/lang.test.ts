/** Unit tests: the supported languages are exactly Spanish, Portuguese and English, each with a catalog. */
import { describe, expect, it } from 'vitest'
import { CATALOGS } from './catalogs'
import { LANGUAGES, parseLang, startingLanguage } from './lang'

describe('LANGUAGES', () => {
  it('names exactly the three supported languages', () => {
    expect(LANGUAGES).toEqual(['es', 'pt', 'en'])
  })

  it('has a catalog for every language it names', () => {
    for (const lang of LANGUAGES) {
      expect(CATALOGS[lang]).toBeDefined()
    }
  })
})

describe('parseLang', () => {
  it.each(LANGUAGES)('accepts %s', (lang) => {
    expect(parseLang(lang)).toBe(lang)
  })

  it.each([['fr'], ['EN'], ['pt-BR'], [''], [undefined], [null], [7]])(
    'rejects %j, which is not exactly one of the three',
    (value) => {
      expect(parseLang(value)).toBeUndefined()
    },
  )
})

describe('startingLanguage', () => {
  it('prefers the language already chosen over the browser language', () => {
    expect(startingLanguage('pt', 'en-US')).toBe('pt')
  })

  it.each([
    ['pt-BR', 'pt'],
    ['PT-br', 'pt'],
    ['en_GB', 'en'],
    ['es', 'es'],
    ['en', 'en'],
  ])('reads the browser language %s by its primary subtag', (browser, expected) => {
    expect(startingLanguage(undefined, browser)).toBe(expected)
  })

  it.each([['fr-FR'], ['de'], [''], ['-'], [undefined]])(
    'falls back to Spanish for the browser language %j',
    (browser) => {
      expect(startingLanguage(undefined, browser)).toBe('es')
    },
  )

  it('ignores an invalid chosen language and uses the browser language', () => {
    expect(startingLanguage('fr', 'pt-BR')).toBe('pt')
    expect(startingLanguage(null, 'en-US')).toBe('en')
  })
})
