export const LANGUAGES = ['es', 'pt', 'en'] as const

export type Lang = (typeof LANGUAGES)[number]
