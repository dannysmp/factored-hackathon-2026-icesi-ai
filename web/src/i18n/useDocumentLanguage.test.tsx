/** Hook test: the document language and title follow the language the page is shown in. */
import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Lang } from './lang'
import { useDocumentLanguage } from './useDocumentLanguage'

describe('useDocumentLanguage', () => {
  it('sets the document language and title, and follows a change of language', () => {
    const { rerender } = renderHook(
      ({ lang, title }: { lang: Lang; title: string }) => {
        useDocumentLanguage(lang, title)
      },
      { initialProps: { lang: 'es', title: 'Disputa de transacciones' } },
    )

    expect(document.documentElement.lang).toBe('es')
    expect(document.title).toBe('Disputa de transacciones')

    rerender({ lang: 'pt', title: 'Contestação de transações' })

    expect(document.documentElement.lang).toBe('pt')
    expect(document.title).toBe('Contestação de transações')
  })
})
