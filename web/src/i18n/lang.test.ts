import { describe, expect, it } from 'vitest'
import { CATALOGS } from './catalogs'
import { LANGUAGES } from './lang'

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
