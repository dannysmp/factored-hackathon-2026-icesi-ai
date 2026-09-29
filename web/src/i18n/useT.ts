import { useMemo } from 'react'
import { CATALOGS } from './catalogs'
import type { Lang } from './lang'
import type { Messages } from './messages'

/**
 * `lang` is prop-threaded, never read from React Context: the same value already flows from the
 * dialogue state (`TurnResponse.lang` mid-conversation, the selected persona's `language` pre-auth)
 * to whichever component calls this hook, so a second, parallel state mechanism would only add a
 * way for the two to drift.
 */
export function useT(lang: Lang): (key: keyof Messages) => string {
  return useMemo(() => {
    const catalog = CATALOGS[lang]
    return (key: keyof Messages) => catalog[key]
  }, [lang])
}
