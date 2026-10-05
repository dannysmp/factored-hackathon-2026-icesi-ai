/** Unit tests: `useT` reads from the catalog matching its `lang` argument, and switches catalogs
 * when a caller re-renders it with a different `lang` — never from Context, only the argument. */
import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { en } from './en'
import { es } from './es'
import type { Lang } from './lang'
import { useT } from './useT'

describe('useT', () => {
  it('reads strings from the catalog matching the given language', () => {
    const { result } = renderHook(() => useT('es'))

    expect(result.current('chat.send')).toBe(es['chat.send'])
  })

  it('switches to the new language’s catalog when re-rendered with a different lang', () => {
    const { result, rerender } = renderHook(({ lang }: { lang: Lang }) => useT(lang), {
      initialProps: { lang: 'es' },
    })
    expect(result.current('common.retry')).toBe(es['common.retry'])

    rerender({ lang: 'en' })

    expect(result.current('common.retry')).toBe(en['common.retry'])
  })
})
