/** A hook that keeps the document's `lang` attribute and title in step with the page language. */
import { useEffect } from 'react'
import type { Lang } from './lang'

/**
 * Keeps the document's `lang` attribute and title in step with the language the page is shown in,
 * so a screen reader pronounces the page with the right voice and the browser tab, history and
 * bookmarks name it in the person's own language.
 */
export function useDocumentLanguage(lang: Lang, title: string): void {
  useEffect(() => {
    document.documentElement.lang = lang
    document.title = title
  }, [lang, title])
}
