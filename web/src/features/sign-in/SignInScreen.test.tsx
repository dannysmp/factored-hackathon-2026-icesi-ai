/** Component test: the sign-in screen's four states and its accessibility. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInScreen } from './SignInScreen'
import { en } from '../../i18n/en'
import { es } from '../../i18n/es'

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

    expect(await screen.findByLabelText(es['signin.personaLabel'])).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Ana' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Emma' })).toBeInTheDocument()
  })

  it('shows a retryable error when the directory cannot be fetched, in the default language', async () => {
    // No persona is known yet at this point, so this state falls back to the screen's own
    // default (Spanish) rather than showing stale or guessed text.
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new Error('network down'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(es['signin.unreachable'])
  })

  it('signs in with the selected persona and access code, then calls onSignedIn', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const signInMock = vi.spyOn(api, 'signIn').mockResolvedValue('token-abc')
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.selectOptions(screen.getByLabelText(es['signin.personaLabel']), 'emma')
    await user.type(screen.getByLabelText(en['signin.accessCodeLabel']), 'the-code')
    await user.click(screen.getByRole('button', { name: en['signin.submit'] }))

    expect(signInMock).toHaveBeenCalledWith('emma', 'the-code', 'customer')
    await screen.findByRole('button', { name: en['signin.submit'] })
    expect(onSignedIn).toHaveBeenCalledWith('token-abc', 'en')
  })

  it('follows the selected customer persona’s own language, switching live as the selection changes', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    // Ana (es) is the first persona, so it's the default selection: the screen already reads
    // Spanish before the customer touches anything.
    expect(await screen.findByRole('button', { name: es['signin.submit'] })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(es['signin.personaLabel']), 'emma')
    expect(screen.getByRole('button', { name: en['signin.submit'] })).toBeInTheDocument()
  })

  it('shows a refusal message and lets the customer retry, without calling onSignedIn', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new Error('refused'))
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

    await screen.findByLabelText(es['signin.personaLabel'])
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

    expect(await screen.findByRole('option', { name: 'Diego' })).toBeInTheDocument()
    expect(fetchCustomerSpy).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    expect(signInMock).toHaveBeenCalledWith('diego', 'agent-code', 'agent')
    expect(onSignedIn).toHaveBeenCalledWith('agent-token', 'pt')
  })

  it('stays fixed-Spanish for the agent audience regardless of the selected persona’s own language (D91)', async () => {
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

    expect(onSignedIn).toHaveBeenCalledWith('token-xyz', 'es')
  })
})
