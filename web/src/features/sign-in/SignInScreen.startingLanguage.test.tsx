/** Component test: the language the sign-in speaks before a persona is selected. */
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInError } from './api'
import { SignInScreen } from './SignInScreen'
import { CATALOGS } from '../../i18n/catalogs'
import { LANGUAGES } from '../../i18n/lang'
import type { Lang } from '../../i18n/lang'

const BROWSER: Record<Lang, string> = { es: 'es-CO', pt: 'pt-BR', en: 'en-US' }
const PERSONAS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'joao', display_name: 'João', language: 'pt', audience: 'customer' as const },
  { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' as const },
]

function browserIn(language: string | undefined): void {
  vi.spyOn(window.navigator, 'language', 'get').mockReturnValue(language as never)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe.each(LANGUAGES)('SignInScreen in a browser whose language is %s', (lang) => {
  const messages = CATALOGS[lang]

  it('says the demonstration is loading in that language', () => {
    browserIn(BROWSER[lang])
    vi.spyOn(api, 'fetchCustomerPersonas').mockReturnValue(new Promise(() => undefined))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent(messages['signin.loading'])
  })

  it('says the demonstration is not available in that language', async () => {
    browserIn(BROWSER[lang])
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new SignInError(401, 'Sign in'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByText(messages['signin.unavailable'])).toBeInTheDocument()
  })

  it('says the directory could not be loaded, and offers Retry, in that language', async () => {
    browserIn(BROWSER[lang])
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(messages['signin.unreachable'])
    expect(alert).toHaveTextContent(messages['failure.offline'])
    expect(screen.getByRole('button', { name: messages['common.retry'] })).toBeInTheDocument()
  })

  it('opens the form on the persona who speaks that language', async () => {
    browserIn(BROWSER[lang])
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('heading', { level: 2 })).toHaveTextContent(
      messages['signin.heading'],
    )
    const slug = PERSONAS.find((persona) => persona.language === lang)?.slug
    expect(screen.getByRole('radio', { checked: true })).toHaveAttribute('value', slug)
  })
})

describe('SignInScreen starting language', () => {
  it.each([['fr-FR'], ['de'], [''], [undefined]])(
    'falls back to Spanish for the browser language %j',
    (language) => {
      browserIn(language)
      vi.spyOn(api, 'fetchCustomerPersonas').mockReturnValue(new Promise(() => undefined))
      render(<SignInScreen onSignedIn={vi.fn()} />)

      expect(screen.getByRole('status')).toHaveTextContent(CATALOGS.es['signin.loading'])
    },
  )

  it('speaks the language the ended session was in rather than the browser language', async () => {
    browserIn('en-US')
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<SignInScreen preferredLang="pt" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(CATALOGS.pt['signin.unreachable'])
  })

  it('reports the language it speaks while the directory is still loading', () => {
    browserIn('pt-BR')
    vi.spyOn(api, 'fetchCustomerPersonas').mockReturnValue(new Promise(() => undefined))
    const onLanguageChange = vi.fn()
    render(<SignInScreen onSignedIn={vi.fn()} onLanguageChange={onLanguageChange} />)

    expect(onLanguageChange).toHaveBeenLastCalledWith('pt')
  })

  it('falls back to the first persona when nobody speaks the browser language', async () => {
    browserIn('pt-BR')
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(
      PERSONAS.filter((persona) => persona.language !== 'pt'),
    )
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'ana')
  })

  it('stays Spanish for the agent audience whatever the browser language', async () => {
    browserIn('en-US')
    vi.spyOn(api, 'fetchAgentPersonas').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(CATALOGS.es['signin.unreachable'])
  })

  it('writes nothing to browser storage', async () => {
    browserIn('pt-BR')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('heading', { level: 2 })

    expect(setItem).not.toHaveBeenCalled()
  })
})
