/** Unit test: the avatar initials. */
import { describe, expect, it } from 'vitest'
import { en } from '../../i18n/en'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'
import { personaInitials } from './personaCases'

const CUSTOMER_CASE_KEYS = [
  'signin.persona.ana.case',
  'signin.persona.joao.case',
  'signin.persona.emma.case',
  'signin.persona.carlos.case',
  'signin.persona.mariana.case',
] as const
const INTERNAL_TERMS =
  /eligible|elegib|elegív|high-value|escalat|deriv|encaminh|dispute|disputa|contestaç|on record|historial|histórico/i

describe('persona case lines', () => {
  it.each([
    ['English', en],
    ['Spanish', es],
    ['Portuguese', pt],
  ] as const)('give each customer their own plain-language case in %s', (_name, catalog) => {
    const lines = CUSTOMER_CASE_KEYS.map((key) => catalog[key])

    expect(new Set(lines).size).toBe(CUSTOMER_CASE_KEYS.length)
    for (const line of lines) {
      expect(line).not.toMatch(INTERNAL_TERMS)
    }
  })
})

describe('personaInitials', () => {
  it('takes the first letter of each of the first two words', () => {
    expect(personaInitials('Ana María Gómez')).toBe('AM')
  })

  it('uses a single letter for a one-word name', () => {
    expect(personaInitials('Emma')).toBe('E')
  })

  it('capitalizes lower-case names and ignores extra spaces', () => {
    expect(personaInitials('  joão   silva ')).toBe('JS')
  })

  it('keeps an accented first letter whole', () => {
    expect(personaInitials('Édgar')).toBe('É')
  })
})
