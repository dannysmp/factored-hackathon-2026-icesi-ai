/** The languages the interface is offered in, and how each names itself. */
/** Supported interface languages, Spanish first because it is the product's primary language. */
export const LANGUAGES = ['es', 'pt', 'en'] as const

/** One of `LANGUAGES`: the code that selects a message catalog and sets the document language. */
export type Lang = (typeof LANGUAGES)[number]

/** Each language's own name for itself, shown as it is, so a reader can always find theirs. */
export const LANGUAGE_NAMES: Record<Lang, string> = {
  es: 'Español',
  pt: 'Português',
  en: 'English',
}
