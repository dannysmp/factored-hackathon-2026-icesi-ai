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

/** The language `value` names, or `undefined` when it is not one of `LANGUAGES`. */
export function parseLang(value: unknown): Lang | undefined {
  return (LANGUAGES as readonly unknown[]).includes(value) ? (value as Lang) : undefined
}

/**
 * The language a screen speaks before anything on it has chosen one: the language the person
 * already chose, when it is valid, else the browser's own language by its primary subtag
 * (`pt-BR` is Portuguese), else Spanish, the product's first language.
 */
export function startingLanguage(chosen: unknown, browserLanguage: string | undefined): Lang {
  const primarySubtag = browserLanguage?.split(/[-_]/, 1)[0]?.toLowerCase()
  return parseLang(chosen) ?? parseLang(primarySubtag) ?? 'es'
}
