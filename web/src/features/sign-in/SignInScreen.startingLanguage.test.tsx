/** Component test: the language the sign-in speaks before a persona is selected. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInError } from './api'
import { SignInScreen } from './SignInScreen'
import { CATALOGS } from '../../i18n/catalogs'
import { LANGUAGES } from '../../i18n/lang'
import type { Lang } from '../../i18n/lang'
import { directoryOf } from './personaDirectory'

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
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
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
      directoryOf(PERSONAS.filter((persona) => persona.language !== 'pt')),
    )
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'ana')
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      CATALOGS.es['signin.heading'],
    )
  })

  it('only preselects: the person can pick another, and nothing signs in on its own', async () => {
    browserIn('pt-BR')
    const fetchPersonas = vi
      .spyOn(api, 'fetchCustomerPersonas')
      .mockResolvedValue(directoryOf(PERSONAS))
    const signIn = vi.spyOn(api, 'signIn')
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'joao')
    const submit = screen.getByRole('button', { name: CATALOGS.pt['signin.submit'] })
    expect(submit).toBeDisabled()

    await user.click(screen.getByRole('radio', { name: /^Ana\b/ }))

    expect(screen.getByRole('radio', { checked: true })).toHaveAttribute('value', 'ana')
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      CATALOGS.es['signin.heading'],
    )
    expect(screen.getByRole('button', { name: CATALOGS.es['signin.submit'] })).toBeDisabled()
    expect(fetchPersonas).toHaveBeenCalledTimes(1)
    expect(signIn).not.toHaveBeenCalled()
    expect(onSignedIn).not.toHaveBeenCalled()
  })

  it('still needs the access code before the preselected persona can sign in', async () => {
    browserIn('en-US')
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
    const signIn = vi.spyOn(api, 'signIn').mockResolvedValue('token-abc')
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    await screen.findByRole('radio', { checked: true })
    const submit = screen.getByRole('button', { name: CATALOGS.en['signin.submit'] })
    expect(submit).toBeDisabled()

    await user.type(screen.getByLabelText(CATALOGS.en['signin.accessCodeLabel']), 'the-code')
    expect(submit).toBeEnabled()
    expect(signIn).not.toHaveBeenCalled()
    await user.click(submit)

    expect(signIn).toHaveBeenCalledWith('emma', 'the-code', 'customer')
  })

  it('stays Spanish for the agent audience whatever the browser language', async () => {
    browserIn('en-US')
    vi.spyOn(api, 'fetchAgentPersonas').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(CATALOGS.es['signin.unreachable'])
  })

  it('keeps the first agent selected even when a later agent speaks the browser language', async () => {
    browserIn('es-CO')
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue(
      directoryOf([
        { slug: 'beatriz', display_name: 'Beatriz', language: 'pt', audience: 'agent' },
        { slug: 'diego', display_name: 'Diego', language: 'es', audience: 'agent' },
      ]),
    )
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'beatriz')
  })

  it('writes nothing to browser storage', async () => {
    browserIn('pt-BR')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('heading', { level: 2 })

    expect(setItem).not.toHaveBeenCalled()
  })
})
