/** Component test: the app shell walks sign-in into the live chat, with no accessibility
 * violations at either step. Both the sign-in and the turn endpoint are a mocked `fetch`. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { es } from './i18n/es'

const PERSONAS_BODY = {
  personas: [{ slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' }],
}
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
  lang: 'es',
  reply: 'Hola, ¿en qué puedo ayudarle?',
  reference_date_line: 'Hoy es 18 de junio de 2026.',
  demo_notice: null,
  choices: [],
  next_expected: null,
  end_session: false,
  handoff_ticket: null,
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubTheWholeFlow(): void {
  // Every call this app makes passes a plain string path (never a Request or URL object), so the
  // mock only needs to handle that one shape.
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url === '/v1/auth/demo-personas') return Promise.resolve(jsonResponse(PERSONAS_BODY))
      if (url === '/v1/auth/demo-sessions') return Promise.resolve(jsonResponse(SESSION_BODY))
      if (url === '/v1/turns') return Promise.resolve(jsonResponse(TURN_BODY))
      throw new Error(`unexpected fetch: ${url}`)
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('shows the sign-in screen first, not the chat', async () => {
    stubTheWholeFlow()
    render(<App />)

    expect(await screen.findByLabelText(es['signin.personaLabel'])).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Customer chat' })).not.toBeInTheDocument()
  })

  it('shows the live chat, greeted for real, once sign-in succeeds', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<App />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'the-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    expect(await screen.findByRole('region', { name: 'Customer chat' })).toBeInTheDocument()
    expect(await screen.findByText('Hola, ¿en qué puedo ayudarle?')).toBeInTheDocument()
  })

  it('frames every step with a banner holding the page title and one main landmark', async () => {
    stubTheWholeFlow()
    render(<App />)

    await screen.findByLabelText(es['signin.personaLabel'])
    expect(screen.getByRole('banner')).toContainElement(
      screen.getByRole('heading', { level: 1, name: 'Dispute intake' }),
    )
    expect(screen.getAllByRole('main')).toHaveLength(1)
  })

  it('has no automatically detectable accessibility violations at the sign-in step', async () => {
    stubTheWholeFlow()
    const { container } = render(<App />)

    await screen.findByLabelText(es['signin.personaLabel'])
    expect(await axe(container)).toHaveNoViolations()
  })
})
