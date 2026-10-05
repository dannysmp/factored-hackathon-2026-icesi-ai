/** Component test: the sign-in screen's four states and its accessibility. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInError } from './api'
import { SignInScreen } from './SignInScreen'
import { en } from '../../i18n/en'
import { es } from '../../i18n/es'
import { getPersonaRadio } from './personaRadios'

/** Two customer personas, one speaking Spanish and one English. */
const PERSONAS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' as const },
]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SignInScreen', () => {
  it('shows the persona picker once the directory loads', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(
      await screen.findByRole('group', { name: es['signin.personaGroupLabel'] }),
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Ana — Español' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Emma — English' })).toBeInTheDocument()
  })

  it('shows a retryable error when the directory cannot be fetched, in the default language', async () => {
    // No persona is known yet at this point, so this state falls back to the screen's own
    // default (Spanish) rather than showing stale or guessed text.
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new Error('network down'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(es['signin.unreachable'])
  })

  it.each(['customer', 'agent'] as const)(
    'says plainly that the demonstration is not available, with no form, when both sign-ins are off (%s)',
    async (audience) => {
      const fetcher = audience === 'customer' ? 'fetchCustomerPersonas' : 'fetchAgentPersonas'
      vi.spyOn(api, fetcher).mockRejectedValue(new SignInError(401, 'Sign in required'))
      const { container } = render(<SignInScreen audience={audience} onSignedIn={vi.fn()} />)

      await screen.findByText(es['signin.unavailable'])
      expect(screen.getByRole('status')).toHaveTextContent(es['signin.unavailable'])
      expect(screen.queryByLabelText(es['signin.accessCodeLabel'])).not.toBeInTheDocument()
      expect(screen.queryByRole('button')).not.toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(await axe(container)).toHaveNoViolations()
    },
  )

  it('treats a directory with no persona for its audience as switched off, for the console too', async () => {
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue([])
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    await screen.findByText(es['signin.unavailable'])
    expect(screen.getByRole('status')).toHaveTextContent(es['signin.unavailable'])
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps a failure that is not the switch-off a retryable error, not "not available"', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new SignInError(429, 'Too many'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(es['signin.unreachable'])
    expect(screen.queryByText(es['signin.unavailable'])).not.toBeInTheDocument()
  })

  it('signs in with the selected persona and access code, then calls onSignedIn', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const signInMock = vi.spyOn(api, 'signIn').mockResolvedValue('token-abc')
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })
    await user.click(getPersonaRadio('emma'))
    await user.type(screen.getByLabelText(en['signin.accessCodeLabel']), 'the-code')
    await user.click(screen.getByRole('button', { name: en['signin.submit'] }))

    expect(signInMock).toHaveBeenCalledWith('emma', 'the-code', 'customer')
    await waitFor(() => {
      expect(onSignedIn).toHaveBeenCalledWith('token-abc', 'en', 'emma')
    })
  })

  it('follows the selected customer persona’s own language, switching live as the selection changes', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    // Ana (es) is the first persona, so it's the default selection: the screen already reads
    // Spanish before the customer touches anything.
    expect(await screen.findByRole('button', { name: es['signin.submit'] })).toBeInTheDocument()

    await user.click(getPersonaRadio('emma'))
    expect(screen.getByRole('button', { name: en['signin.submit'] })).toBeInTheDocument()
  })

  it('shows a refusal message and lets the customer retry, without calling onSignedIn', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new SignInError(401, 'Sign-in refused'))
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    await screen.findByLabelText(es['signin.accessCodeLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'wrong')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    // Ana (es), the default selection, is still selected: the refusal message renders in her
    // language, not a hardcoded one.
    expect(await screen.findByRole('alert')).toHaveTextContent(es['signin.refused'])
    expect(onSignedIn).not.toHaveBeenCalled()
  })

  it('has no automatically detectable accessibility violations once ready', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const { container } = render(<SignInScreen onSignedIn={vi.fn()} />)

    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("fetches the agent directory and signs in against the agent broker when audience is 'agent'", async () => {
    const agentPersonas = [
      { slug: 'diego', display_name: 'Diego', language: 'pt', audience: 'agent' as const },
    ]
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue(agentPersonas)
    const fetchCustomerSpy = vi.spyOn(api, 'fetchCustomerPersonas')
    const signInMock = vi.spyOn(api, 'signIn').mockResolvedValue('agent-token')
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen audience="agent" onSignedIn={onSignedIn} />)

    expect(await screen.findByRole('option', { name: 'Diego — Português' })).toBeInTheDocument()
    expect(fetchCustomerSpy).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    expect(signInMock).toHaveBeenCalledWith('diego', 'agent-code', 'agent')
    expect(onSignedIn).toHaveBeenCalledWith('agent-token', 'pt', 'diego')
  })

  it('stays fixed-Spanish for the agent audience regardless of the selected persona’s own language', async () => {
    // Diego's own language is Portuguese — that's what the customer chat would use once signed
    // in, but the console's own sign-in chrome must not switch to it.
    const agentPersonas = [
      { slug: 'diego', display_name: 'Diego', language: 'pt', audience: 'agent' as const },
    ]
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue(agentPersonas)
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('button', { name: es['signin.submit'] })).toBeInTheDocument()
  })

  it('falls back to the default language instead of crashing when a persona’s language is outside es/pt/en', async () => {
    // The wire contract's `language` field is a bare, unvalidated string (it mirrors the
    // backend's own persona model, not scoped to this frontend's three display languages) — a
    // persona carrying something else must degrade gracefully, not blank the whole screen.
    const personasWithOddLanguage = [
      { slug: 'zora', display_name: 'Zora', language: 'klingon', audience: 'customer' as const },
    ]
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(personasWithOddLanguage)
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    expect(await screen.findByRole('button', { name: es['signin.submit'] })).toBeInTheDocument()

    vi.spyOn(api, 'signIn').mockResolvedValue('token-xyz')
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'the-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    expect(onSignedIn).toHaveBeenCalledWith('token-xyz', 'es', 'zora')
  })
})
