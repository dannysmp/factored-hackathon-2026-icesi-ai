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

/** A plain date (`2026-06-18`) as "18 jun 2026". */
export function formatDate(isoDate: string): string {
  return DATE.format(new Date(`${isoDate}T00:00:00Z`))
}

/** A moment (`2026-06-18T14:05:00Z`) as "18 jun 2026, 14:05 UTC". */
export function formatDateTime(isoDateTime: string): string {
  return DATE_TIME.format(new Date(isoDateTime))
}

/** A decimal-string amount and its currency code as "250,00 MXN"; the code stays so two currencies are never confused. */
export function formatMoney(amount: string, currency: string): string {
  return new Intl.NumberFormat(LOCALE, {
    style: 'currency',
    currency,
    currencyDisplay: 'code',
    minimumFractionDigits: 2,
  }).format(Number(amount))
}

/** A score between 0 and 1 as a percentage, "37 %". */
export function formatShare(value: number): string {
  return new Intl.NumberFormat(LOCALE, { style: 'percent', maximumFractionDigits: 0 }).format(value)
}

/** How old a ticket is, in words: "Hoy", "1 día", "3 días". */
export function formatAge(days: number): string {
  if (days === 0) return 'Hoy'
  return `${String(days)} ${days === 1 ? 'día' : 'días'}`
}
