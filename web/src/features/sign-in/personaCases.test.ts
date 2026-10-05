/** Unit test: the avatar initials. */
import { describe, expect, it } from 'vitest'
import { personaInitials } from './personaCases'

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
