/**
 * How the console writes dates, times, amounts and ages for an agent reading Spanish.
 *
 * Every date on the wire is an ISO value in UTC, so each one is formatted in UTC: the same ticket
 * reads the same on every machine, whatever time zone the agent's browser is set to.
 */

const LOCALE = 'es'

const DATE = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

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

const MOST_AMOUNT_DECIMALS = 4

/** The decimals an amount string really carries: "48.50" has one, "15000.00" none. */
function significantDecimals(amount: string): number {
  const fraction = amount.split('.')[1]?.replace(/0+$/, '') ?? ''
  return Math.min(fraction.length, MOST_AMOUNT_DECIMALS)
}

/**
 * A decimal-string amount and its currency code as "250,00 MXN"; the code stays so two currencies
 * are never confused. The currency decides the decimals (two for the peso, none for the Chilean
 * peso) and an amount that carries more keeps them. An amount that is not a number reads "—"; a
 * code `Intl` does not accept is shown as given.
 */
export function formatMoney(amount: string, currency: string): string {
  const value = Number(amount)
  if (amount.trim() === '' || !Number.isFinite(value)) return UNREADABLE
  const usual = currencyFormat(currency)
  if (usual === null) return `${amount} ${currency}`
  const own = significantDecimals(amount)
  const format =
    own > (usual.resolvedOptions().minimumFractionDigits ?? 0)
      ? (currencyFormat(currency, own) ?? usual)
      : usual
  return format.format(value)
}

function shareFormat(digits: number): Intl.NumberFormat {
  return new Intl.NumberFormat(LOCALE, {
    style: 'percent',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

const WHOLE_SHARE = shareFormat(0)
const MOST_DECIMALS = 3
const FINEST_SHARE = shareFormat(MOST_DECIMALS)
const SHARE_BY_DECIMALS = [WHOLE_SHARE, shareFormat(1), shareFormat(2), FINEST_SHARE]

/** A score between 0 and 1 as a percentage, "37 %"; a value that is not a number as "—". */
export function formatShare(value: number): string {
  return Number.isFinite(value) ? WHOLE_SHARE.format(value) : UNREADABLE
}

/**
 * A risk score as a percentage, written so that a score under its threshold never reads equal to
 * it: when both round to the same whole percentage, the score gains decimals until they differ
 * (up to three).
 */
export function formatScore(score: number, threshold: number): string {
  if (!Number.isFinite(score)) return UNREADABLE
  if (!Number.isFinite(threshold) || score >= threshold) return formatShare(score)
  const decimals = SHARE_BY_DECIMALS.find(
    (format) => format.format(score) !== format.format(threshold),
  )
  return (decimals ?? FINEST_SHARE).format(score)
}

/** How old a ticket is, in words: "Hoy", "1 día", "3 días". */
export function formatAge(days: number): string {
  if (days === 0) return 'Hoy'
  return `${String(days)} ${days === 1 ? 'día' : 'días'}`
}
