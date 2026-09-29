/** Component test: the console shell walks agent sign-in into the queue, with no accessibility
 * violations at either step. The agent sign-in is a mocked `fetch`; the queue itself is the
 * module-scope fixture client. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsoleApp } from './ConsoleApp'

const PERSONAS_BODY = {
  personas: [{ slug: 'diego', display_name: 'Diego', language: 'es', audience: 'agent' }],
}
const SESSION_BODY = {
  access_token: 'agent-token',
  token_type: 'Bearer',
  expires_at: '2026-09-28T13:00:00Z',
  expires_in: 3600,
}

function stubTheWholeFlow(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url === '/v1/auth/demo-personas') {
        return Promise.resolve(new Response(JSON.stringify(PERSONAS_BODY), { status: 200 }))
      }
      if (url === '/v1/auth/demo-agent-sessions') {
        return Promise.resolve(new Response(JSON.stringify(SESSION_BODY), { status: 200 }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConsoleApp', () => {
  it('shows the agent sign-in screen first, not the queue', async () => {
    stubTheWholeFlow()
    render(<ConsoleApp />)

    expect(await screen.findByLabelText('Persona')).toBeInTheDocument()
    expect(
      screen.queryByRole('region', { name: 'Cola de casos escalados' }),
    ).not.toBeInTheDocument()
  })

  it('shows the queue once the agent signs in', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText('Persona')
    await user.type(screen.getByLabelText('Access code'), 'agent-code')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByRole('region', { name: 'Cola de casos escalados' }),
    ).toBeInTheDocument()
  })

  it('shows a ticket’s detail once its reference is clicked, then returns to the queue', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText('Persona')
    await user.type(screen.getByLabelText('Access code'), 'agent-code')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    const rows = await screen.findAllByRole('row')
    const firstTicketRefButton = rows[1]?.querySelector('button')
    if (firstTicketRefButton === null || firstTicketRefButton === undefined) {
      throw new Error('expected the first row to contain a ticket reference button')
    }

    await user.click(firstTicketRefButton)

    expect(await screen.findByRole('region', { name: 'Detalle del ticket' })).toBeInTheDocument()
    expect(
      screen.queryByRole('region', { name: 'Cola de casos escalados' }),
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Volver a la cola' }))

    expect(
      await screen.findByRole('region', { name: 'Cola de casos escalados' }),
    ).toBeInTheDocument()
  })

  it('has no automatically detectable accessibility violations at the sign-in step', async () => {
    stubTheWholeFlow()
    const { container } = render(<ConsoleApp />)

    await screen.findByLabelText('Persona')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations once the queue renders', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    const { container } = render(<ConsoleApp />)

    await screen.findByLabelText('Persona')
    await user.type(screen.getByLabelText('Access code'), 'agent-code')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByRole('region', { name: 'Cola de casos escalados' })

    expect(await axe(container)).toHaveNoViolations()
  })
})
