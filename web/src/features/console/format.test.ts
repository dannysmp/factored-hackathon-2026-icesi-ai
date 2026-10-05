/** Unit test: how the console writes dates, amounts, shares and ages for an agent reading Spanish. */
import { describe, expect, it } from 'vitest'
import { formatAge, formatDate, formatDateTime, formatMoney, formatShare } from './format'

describe('formatDate', () => {
  it('writes a plain date as day, short month and year', () => {
    expect(formatDate('2026-06-18')).toBe('18 jun 2026')
  })

  it('does not move the day across a time zone boundary', () => {
    expect(formatDate('2026-01-01')).toBe('1 ene 2026')
    expect(formatDate('2026-12-31')).toBe('31 dic 2026')
  })
})

describe('formatDateTime', () => {
  it('writes the moment with its time in UTC, named as such', () => {
    expect(formatDateTime('2026-06-18T14:05:00Z')).toBe('18 jun 2026, 14:05 UTC')
  })

  it('reads midnight as 00:00, not 24:00', () => {
    expect(formatDateTime('2026-06-18T00:00:00Z')).toBe('18 jun 2026, 00:00 UTC')
  })
})

describe('formatMoney', () => {
  it('writes the amount with two decimals and the currency code after it', () => {
    expect(formatMoney('250', 'MXN').replaceAll(' ', ' ')).toBe('250,00 MXN')
    expect(formatMoney('48.5', 'USD').replaceAll(' ', ' ')).toBe('48,50 USD')
  })

  it('groups thousands', () => {
    expect(formatMoney('1234567.89', 'COP').replaceAll(' ', ' ')).toBe('1.234.567,89 COP')
  })
})

describe('formatShare', () => {
  it('writes a score as a whole percentage', () => {
    expect(formatShare(0.37).replaceAll(' ', ' ')).toBe('37 %')
    expect(formatShare(0.03).replaceAll(' ', ' ')).toBe('3 %')
    expect(formatShare(1).replaceAll(' ', ' ')).toBe('100 %')
  })
})

describe('formatAge', () => {
  it('writes zero days as today, one as a day and more as days', () => {
    expect(formatAge(0)).toBe('Hoy')
    expect(formatAge(1)).toBe('1 día')
    expect(formatAge(12)).toBe('12 días')
  })
})
