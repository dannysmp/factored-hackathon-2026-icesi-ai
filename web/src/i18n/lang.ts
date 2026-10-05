export const LANGUAGES = ['es', 'pt', 'en'] as const

export type Lang = (typeof LANGUAGES)[number]

/** Each language's own name for itself, shown as it is, so a reader can always find theirs. */
export const LANGUAGE_NAMES: Record<Lang, string> = {
  es: 'Español',
  pt: 'Português',
  en: 'English',
}
