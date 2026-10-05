/** Component test: a session the service ends returns the person to the sign-in, saying so. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { findMessage } from './features/customer-chat/findMessage'
import { en } from './i18n/en'
import { es } from './i18n/es'
import { findPersonaRadio } from './features/sign-in/personaRadios'

const SESSION_BODY = {
  access_token: 'token-abc',
  token_type: 'Bearer',
  expires_at: '2026-09-28T12:30:00Z',
  expires_in: 1800,
}
const TURN_BODY = {
  contract_version: '1',
  turn_id: 'server-turn-00000001',
  conversation_id: 'conversation-1',
  state_version: 1,
  lang: 'en',
  reply: 'Hello, how can I help?',
  reference_date_line: 'Today is 18 June 2026.',
  demo_notice: null,
  choices: [],
  next_expected: null,
  end_session: false,
  handoff_ticket: null,
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

interface ServiceOptions {
  spanishPersona?: boolean
  secondEnglishPersona?: boolean
  turnLang?: 'en' | 'es'
}

function stubService(
  turnStatuses: number[],
  { spanishPersona = false, secondEnglishPersona = false, turnLang = 'en' }: ServiceOptions = {},
): void {
  const remaining = [...turnStatuses]
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url === '/v1/auth/demo-personas') {
        return Promise.resolve(
          json(200, {
            personas: [
              { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' },
              ...(secondEnglishPersona
                ? [{ slug: 'noah', display_name: 'Noah', language: 'en', audience: 'customer' }]
                : []),
              ...(spanishPersona
                ? [{ slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' }]
                : []),
            ],
          }),
        )
      }
      if (url === '/v1/auth/demo-sessions') return Promise.resolve(json(201, SESSION_BODY))
      const status = remaining.shift() ?? 200
      return Promise.resolve(
        status === 200
          ? json(200, { ...TURN_BODY, lang: turnLang })
          : json(status, { title: 'Sign in required' }),
      )
    }),
  )
}

async function signIn(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(await screen.findByLabelText(en['signin.accessCodeLabel']), 'the-code')
  await user.click(screen.getByRole('button', { name: en['signin.submit'] }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App when the session ends', () => {
  it('returns to the sign-in with a note, in the language of the ended session', async () => {
    stubService([200, 401])
    const user = userEvent.setup()
    const { container } = render(<App />)
    await signIn(user)
    await findMessage(TURN_BODY.reply)
    await user.type(screen.getByLabelText(en['chat.messageLabel']), 'hello')
    await user.click(screen.getByRole('button', { name: en['chat.send'] }))

    await waitFor(() => {
      expect(screen.getByText(en['app.sessionExpired']).closest('[role="status"]')).not.toBeNull()
    })
    expect(screen.queryByRole('region', { name: en['chat.regionLabel'] })).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2 })).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it('brings back a form in the language of the ended session, so the note and the form agree', async () => {
    stubService([401], { spanishPersona: true })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await findPersonaRadio('ana'))
    await user.type(await screen.findByLabelText(es['signin.accessCodeLabel']), 'the-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    await waitFor(() => {
      expect(screen.getByText(es['app.sessionExpired']).closest('[role="status"]')).not.toBeNull()
    })
    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'ana')
    expect(screen.getByRole('button', { name: es['signin.submit'] })).toBeInTheDocument()
  })

  it('follows the language the conversation moved to, not the persona it started with', async () => {
    vi.spyOn(window.navigator, 'language', 'get').mockReturnValue('en-US')
    stubService([200, 401], { spanishPersona: true, turnLang: 'es' })
    const user = userEvent.setup()
    render(<App />)
    await signIn(user)
    await user.type(await screen.findByLabelText(es['chat.messageLabel']), 'hola')
    await user.click(screen.getByRole('button', { name: es['chat.send'] }))

    await waitFor(() => {
      expect(screen.getByText(es['app.sessionExpired']).closest('[role="status"]')).not.toBeNull()
    })
    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'ana')
    expect(document.documentElement.lang).toBe('es')
  })

  it('offers the same person again when more than one speaks the language', async () => {
    stubService([401], { secondEnglishPersona: true })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await findPersonaRadio('noah'))
    await signIn(user)

    await screen.findByText(en['app.sessionExpired'])
    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'noah')
  })

  it('puts the keyboard on the form so the person can sign in again at once', async () => {
    stubService([401])
    const user = userEvent.setup()
    render(<App />)
    await signIn(user)

    await waitFor(() => {
      expect(screen.getByRole('radio', { checked: true })).toHaveFocus()
    })
  })

  it('takes the note away once the person has signed in again', async () => {
    stubService([401, 200])
    const user = userEvent.setup()
    render(<App />)
    await signIn(user)
    await screen.findByText(en['app.sessionExpired'])
    await signIn(user)

    await findMessage(TURN_BODY.reply)
    expect(screen.queryByText(en['app.sessionExpired'])).not.toBeInTheDocument()
  })

  it('does not bring the note back when the person later signs out by choice', async () => {
    stubService([401, 200])
    const user = userEvent.setup()
    render(<App />)
    await signIn(user)
    await screen.findByText(en['app.sessionExpired'])
    await signIn(user)
    await findMessage(TURN_BODY.reply)
    await user.click(screen.getByRole('button', { name: en['app.signOut'] }))

    await screen.findByRole('group', { name: en['signin.personaGroupLabel'] })
    expect(screen.queryByText(en['app.sessionExpired'])).not.toBeInTheDocument()
  })

  it('shows no note on a first visit or after signing out by choice', async () => {
    stubService([200])
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('group', { name: en['signin.personaGroupLabel'] })
    await signIn(user)
    await findMessage(TURN_BODY.reply)
    await user.click(screen.getByRole('button', { name: en['app.signOut'] }))

    await screen.findByRole('group', { name: en['signin.personaGroupLabel'] })
    expect(screen.queryByText(en['app.sessionExpired'])).not.toBeInTheDocument()
  })
})
