import { en } from './en'
import { es } from './es'
import type { Lang } from './lang'
import type { Messages } from './messages'
import { pt } from './pt'

export const CATALOGS: Record<Lang, Messages> = { es, pt, en }
