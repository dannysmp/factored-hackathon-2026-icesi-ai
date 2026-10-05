/**
 * How the console writes dates, times, amounts and ages for an agent reading Spanish.
 *
 * Every date on the wire is an ISO value in UTC, so each one is formatted in UTC: the same ticket
 * reads the same on every machine, whatever time zone the agent's browser is set to.
 */

/** The console is deliberately fixed-Spanish and never uses the per-language catalogs. */
const LOCALE = 'es'

/** Day, abbreviated month and year, in UTC. */
const DATE = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

/** `DATE` plus a 24-hour time and the zone name, in UTC. */
const DATE_TIME = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'UTC',
  timeZoneName: 'short',
})

/** What stands in for a value the console cannot read, so one malformed field never blanks the screen. */
const UNREADABLE = '—'

function isReadable(moment: Date): boolean {
  return !Number.isNaN(moment.getTime())
}

/** A plain date (`2026-06-18`) as "18 jun 2026"; a value that is not a date as "—". */
export function formatDate(isoDate: string): string {
  const moment = new Date(`${isoDate}T00:00:00Z`)
  return isReadable(moment) ? DATE.format(moment) : UNREADABLE
}

/** A moment (`2026-06-18T14:05:00Z`) as "18 jun 2026, 14:05 UTC"; a value that is not a moment as "—". */
export function formatDateTime(isoDateTime: string): string {
  const moment = new Date(isoDateTime)
  return isReadable(moment) ? DATE_TIME.format(moment) : UNREADABLE
}

const CURRENCY_FORMATS = new Map<string, Intl.NumberFormat>()

/** The shared formatter for one currency code, or `null` when the code is not one `Intl` accepts. */
function currencyFormat(currency: string, decimals?: number): Intl.NumberFormat | null {
  const key = `${currency}/${String(decimals ?? '')}`
  const known = CURRENCY_FORMATS.get(key)
  if (known !== undefined) return known
  try {
    const created = new Intl.NumberFormat(LOCALE, {
      style: 'currency',
      currency,
      currencyDisplay: 'code',
      ...(decimals === undefined
        ? {}
        : { minimumFractionDigits: decimals, maximumFractionDigits: decimals }),
    })
    CURRENCY_FORMATS.set(key, created)
    return created
  } catch {
    return null
  }
}

/** A plain decimal string: optional minus, digits, an optional fraction. Rules out hex, exponents and spaces. */
const DECIMAL_STRING = /^-?\d+(\.\d+)?$/

/** A fraction with a digit other than zero: "48.50" has one, "15000.00" does not. */
const NONZERO_FRACTION = /\.\d*[1-9]/

/** The wire carries at most two decimals, so two is what an amount's own cents need. */
const CENTS = 2

/**
 * A decimal-string amount and its currency code as "250,00 MXN"; the code stays so two currencies
 * are never confused. The currency decides the decimals (two for the peso, none for the Chilean
 * peso); an amount with a nonzero fraction is never shortened below cents, so "15000.50" in a
 * currency without decimals still reads "15.000,50 CLP". An amount that is not a plain decimal
 * number reads "—"; a missing or unrecognized code leaves the amount as given.
 */
export function formatMoney(amount: string, currency: string): string {
  const text = amount.trim()
  if (!DECIMAL_STRING.test(text)) return UNREADABLE
  const usual = currencyFormat(currency)
  if (usual === null) return `${text} ${currency}`.trim()
  const usualDecimals = usual.resolvedOptions().minimumFractionDigits ?? 0
  const needsCents = NONZERO_FRACTION.test(text) && usualDecimals < CENTS
  const format = needsCents ? (currencyFormat(currency, CENTS) ?? usual) : usual
  const value = Number(text)
  return format.format(value === 0 ? 0 : value)
}

function shareFormat(digits: number): Intl.NumberFormat {
  return new Intl.NumberFormat(LOCALE, {
    style: 'percent',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

const WHOLE_SHARE = shareFormat(0)

/** The most decimals a share is written with: a score and a threshold closer than this read alike. */
const MOST_DECIMALS = 3
const FINEST_SHARE = shareFormat(MOST_DECIMALS)

/** The coarser formatters tried before `FINEST_SHARE`, from none to two decimals. */
const COARSER_SHARES = [WHOLE_SHARE, shareFormat(1), shareFormat(2)]

/** A score between 0 and 1 as a percentage, "37 %"; a value that is not a number as "—". */
export function formatShare(value: number): string {
  return Number.isFinite(value) ? WHOLE_SHARE.format(value) : UNREADABLE
}

/** A risk score and the threshold it is compared with, written for the same screen. */
export interface ScoreAgainstThreshold {
  score: string
  threshold: string
}

/**
 * A risk score and its escalation threshold as percentages, both written with the same decimals.
 * A score at or above its threshold, or either value unreadable, is written whole. A score under
 * its threshold gains decimals, on both values, until the two read differently, so a score never
 * reads equal to a threshold it has not reached; two values closer than three decimals can tell
 * apart are written to three, the finest precision shown.
 */
export function formatScoreAgainstThreshold(
  score: number,
  threshold: number,
): ScoreAgainstThreshold {
  const whole = { score: formatShare(score), threshold: formatShare(threshold) }
  if (!Number.isFinite(score) || !Number.isFinite(threshold) || score >= threshold) return whole
  const format =
    COARSER_SHARES.find((candidate) => candidate.format(score) !== candidate.format(threshold)) ??
    FINEST_SHARE
  return { score: format.format(score), threshold: format.format(threshold) }
}

/** How old a ticket is, in words: "Hoy", "1 día", "3 días". */
export function formatAge(days: number): string {
  if (days === 0) return 'Hoy'
  return `${String(days)} ${days === 1 ? 'día' : 'días'}`
}
