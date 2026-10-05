/** The registry of message catalogs, one per supported language. */
import { en } from './en'
import { es } from './es'
import type { Lang } from './lang'
import type { Messages } from './messages'
import { pt } from './pt'

/**
 * Every catalog keyed by language. Typed as `Record<Lang, Messages>`, so a missing language or a
 * missing key is a compile error; `catalogs.test.ts` also checks for blank values.
 */
export const CATALOGS: Record<Lang, Messages> = { es, pt, en }
