/** Unit test: how the console writes dates, amounts, shares and ages for an agent reading Spanish. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  formatAge,
  formatDate,
  formatDateTime,
  formatMoney,
  formatScore,
  formatShare,
} from './format'

describe('formatDate', () => {
  it('writes a plain date as day, short month and year', () => {
    expect(formatDate('2026-06-18')).toBe('18 jun 2026')
  })

  it('does not move the day across a time zone boundary', () => {
    expect(formatDate('2026-01-01')).toBe('1 ene 2026')
    expect(formatDate('2026-12-31')).toBe('31 dic 2026')
  })
})

describe('formatDate and formatDateTime on a value that is not a date', () => {
  it('write a dash rather than "Invalid Date"', () => {
    expect(formatDate('not-a-date')).toBe('—')
    expect(formatDate('')).toBe('—')
    expect(formatDateTime('not-a-moment')).toBe('—')
    expect(formatDateTime('')).toBe('—')
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

describe('formatMoney on unusual input', () => {
  it('lets the currency decide the decimals', () => {
    expect(formatMoney('15000', 'CLP').replaceAll('\u00a0', ' ')).toBe('15.000 CLP')
    expect(formatMoney('15000.00', 'CLP').replaceAll('\u00a0', ' ')).toBe('15.000 CLP')
    expect(formatMoney('1500', 'JPY').replaceAll('\u00a0', ' ')).toBe('1500 JPY')
  })

  it('keeps the decimals an amount carries beyond its currency’s own', () => {
    expect(formatMoney('12.345', 'MXN').replaceAll('\u00a0', ' ')).toBe('12,345 MXN')
    expect(formatMoney('12.50', 'MXN').replaceAll('\u00a0', ' ')).toBe('12,50 MXN')
  })

  it('writes a dash for an amount that is not a number', () => {
    expect(formatMoney('abc', 'MXN')).toBe('—')
    expect(formatMoney('', 'MXN')).toBe('—')
    expect(formatMoney('Infinity', 'MXN')).toBe('—')
  })

  it('shows the amount and the code as given when the code is not a currency', () => {
    expect(formatMoney('250', '12')).toBe('250 12')
  })

  describe('building the formatter', () => {
    afterEach(() => {
      vi.restoreAllMocks()
    })

    it('does it once per currency, not on every call', () => {
      const build = vi.spyOn(Intl, 'NumberFormat')

      const first = formatMoney('1', 'BRL')
      const second = formatMoney('2', 'BRL')

      expect(first).not.toBe(second)
      expect(build).toHaveBeenCalledTimes(1)
    })
  })
})

describe('formatShare on a value that is not a number', () => {
  it('writes a dash', () => {
    expect(formatShare(Number.NaN)).toBe('—')
    expect(formatShare(Number.POSITIVE_INFINITY)).toBe('—')
  })
})

describe('formatScore', () => {
  const plain = (text: string): string => text.replaceAll('\u00a0', ' ')

  it('writes a whole percentage when the score and threshold already differ', () => {
    expect(plain(formatScore(0.2, 0.4))).toBe('20 %')
  })

  it('adds decimals to a score under its threshold until the two no longer read alike', () => {
    expect(plain(formatScore(0.371, 0.374))).toBe('37,1 %')
    expect(plain(formatScore(0.3996, 0.4001))).toBe('39,96 %')
    expect(plain(formatScore(0.3999, 0.4))).toBe('39,99 %')
  })

  it('stops at three decimals when the two still cannot be told apart', () => {
    expect(plain(formatScore(0.399996, 0.4))).toBe('40,000 %')
  })

  it('keeps a score at or above its threshold whole', () => {
    expect(plain(formatScore(0.375, 0.375))).toBe('38 %')
    expect(plain(formatScore(0.9, 0.4))).toBe('90 %')
  })

  it('writes a dash for a score that is not a number, and a whole one when the threshold is not', () => {
    expect(formatScore(Number.NaN, 0.4)).toBe('—')
    expect(plain(formatScore(0.2, Number.NaN))).toBe('20 %')
  })
})

describe('formatShare', () => {
  it('writes a score as a whole percentage', () => {
    expect(formatShare(0.37).replaceAll(' ', ' ')).toBe('37 %')
    expect(formatShare(0.03).replaceAll(' ', ' ')).toBe('3 %')
    expect(formatShare(1).replaceAll(' ', ' ')).toBe('100 %')
  })
})

describe('formatShare rounding', () => {
  it('rounds to the nearest whole percentage, in both directions', () => {
    expect(formatShare(0.375).replaceAll('\u00a0', ' ')).toBe('38 %')
    expect(formatShare(0.374).replaceAll('\u00a0', ' ')).toBe('37 %')
  })
})

describe('formatAge', () => {
  it('writes zero days as today, one as a day and more as days', () => {
    expect(formatAge(0)).toBe('Hoy')
    expect(formatAge(1)).toBe('1 día')
    expect(formatAge(12)).toBe('12 días')
  })
})
