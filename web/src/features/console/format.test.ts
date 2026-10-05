/** Unit test: how the console writes dates, amounts, shares and ages for an agent reading Spanish. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  formatAge,
  formatDate,
  formatDateTime,
  formatMoney,
  formatScoreAgainstThreshold,
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

  it('never shortens an amount with a nonzero fraction below cents', () => {
    expect(formatMoney('15000.50', 'CLP').replaceAll('\u00a0', ' ')).toBe('15.000,50 CLP')
    expect(formatMoney('0.10', 'JPY').replaceAll('\u00a0', ' ')).toBe('0,10 JPY')
    expect(formatMoney('1234567.89', 'COP').replaceAll('\u00a0', ' ')).toBe('1.234.567,89 COP')
    expect(formatMoney('12.50', 'MXN').replaceAll('\u00a0', ' ')).toBe('12,50 MXN')
  })

  it('writes a negative zero as zero', () => {
    expect(formatMoney('-0.00', 'MXN').replaceAll('\u00a0', ' ')).toBe('0,00 MXN')
  })

  it('writes a dash for an amount that is not a number', () => {
    expect(formatMoney('abc', 'MXN')).toBe('—')
    expect(formatMoney('', 'MXN')).toBe('—')
    expect(formatMoney('Infinity', 'MXN')).toBe('—')
    expect(formatMoney('0x10', 'MXN')).toBe('—')
    expect(formatMoney('1e3', 'MXN')).toBe('—')
  })

  it('shows the amount and the code as given when the code is not a currency', () => {
    expect(formatMoney('250', '12')).toBe('250 12')
  })

  it('shows the bare amount when the code is missing', () => {
    expect(formatMoney('250', '')).toBe('250')
    expect(formatMoney('250', '  ')).toBe('250')
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

describe('formatScoreAgainstThreshold', () => {
  const plain = (shares: {
    score: string
    threshold: string
  }): { score: string; threshold: string } => ({
    score: shares.score.replaceAll('\u00a0', ' '),
    threshold: shares.threshold.replaceAll('\u00a0', ' '),
  })

  it('writes whole percentages when the score and threshold already differ', () => {
    expect(plain(formatScoreAgainstThreshold(0.2, 0.4))).toEqual({
      score: '20 %',
      threshold: '40 %',
    })
  })

  it('writes both with the decimals at which a score under its threshold reads differently', () => {
    expect(plain(formatScoreAgainstThreshold(0.371, 0.374))).toEqual({
      score: '37,1 %',
      threshold: '37,4 %',
    })
    expect(plain(formatScoreAgainstThreshold(0.3996, 0.4001))).toEqual({
      score: '39,96 %',
      threshold: '40,01 %',
    })
  })

  it('goes to three decimals when two do not tell the pair apart', () => {
    expect(plain(formatScoreAgainstThreshold(0.39996, 0.4))).toEqual({
      score: '39,996 %',
      threshold: '40,000 %',
    })
    expect(plain(formatScoreAgainstThreshold(0.3999, 0.4))).toEqual({
      score: '39,99 %',
      threshold: '40,00 %',
    })
  })

  it('never writes a score under its threshold as equal to it, to the finest precision shown', () => {
    const pairs: [number, number][] = [
      [0.3996, 0.404],
      [0.4, 0.40004],
      [0.371, 0.374],
      [0.39996, 0.4],
      [0.1999, 0.2],
    ]
    for (const [score, threshold] of pairs) {
      const shown = formatScoreAgainstThreshold(score, threshold)
      expect(shown.score).not.toBe(shown.threshold)
    }
  })

  it('writes the same text for both once the values are closer than the finest precision', () => {
    expect(plain(formatScoreAgainstThreshold(0.399996, 0.4))).toEqual({
      score: '40,000 %',
      threshold: '40,000 %',
    })
  })

  it('keeps a score at or above its threshold whole', () => {
    expect(plain(formatScoreAgainstThreshold(0.375, 0.375))).toEqual({
      score: '38 %',
      threshold: '38 %',
    })
    expect(plain(formatScoreAgainstThreshold(0.9, 0.4))).toEqual({
      score: '90 %',
      threshold: '40 %',
    })
  })

  it('writes a dash for a value that is not a number and a whole percentage for the other', () => {
    expect(plain(formatScoreAgainstThreshold(Number.NaN, 0.4))).toEqual({
      score: '—',
      threshold: '40 %',
    })
    expect(plain(formatScoreAgainstThreshold(0.2, Number.NaN))).toEqual({
      score: '20 %',
      threshold: '—',
    })
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
