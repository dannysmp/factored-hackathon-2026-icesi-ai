/** The translation hook: looks up interface strings in the catalog for a given language. */
import { useMemo } from 'react'
import { CATALOGS } from './catalogs'
import type { Lang } from './lang'
import type { Messages } from './messages'

/**
 * Returns a lookup function `t(key)` for the catalog of `lang`; the function keeps its identity
 * until `lang` changes, so it is safe in dependency lists. Keys are limited to those in `Messages`.
 *
 * `lang` is passed in as an argument, never read from React Context: the same value already
 * flows from the dialogue state (`TurnResponse.lang` mid-conversation, the selected persona's
 * `language` before sign-in) to whichever component calls this hook, so a second, parallel state
 * mechanism would only add a way for the two to drift.
 */
export function useT(lang: Lang): (key: keyof Messages) => string {
  return useMemo(() => {
    const catalog = CATALOGS[lang]
    return (key: keyof Messages) => catalog[key]
  }, [lang])
}
