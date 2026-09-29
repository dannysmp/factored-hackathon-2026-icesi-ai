/** Component test: the console shell walks agent sign-in into the queue and a ticket's detail,
 * with no accessibility violations at any step. Every request (sign-in, queue, ticket detail) is
 * a mocked `fetch`, serving the same fixture data `web/src/features/console/fixtures.ts` already
 * validates against the real contracts. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsoleApp } from './ConsoleApp'
import { DEMO_QUEUE, DEMO_TICKET_DETAILS } from './features/console/fixtures'

const PERSONAS_BODY = {
  personas: [{ slug: 'diego', display_name: 'Diego', language: 'es', audience: 'agent' }],
}
const SESSION_BODY = {
  access_token: 'agent-token',
  token_type: 'Bearer',
  expires_at: '2026-09-28T13:00:00Z',
  expires_in: 3600,
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

function stubTheWholeFlow(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url === '/v1/auth/demo-personas') {
        return Promise.resolve(jsonResponse(200, PERSONAS_BODY))
      }
      if (url === '/v1/auth/demo-agent-sessions') {
        return Promise.resolve(jsonResponse(201, SESSION_BODY))
      }
      if (url === '/v1/agent/queue') {
        return Promise.resolve(jsonResponse(200, DEMO_QUEUE))
      }
      const [firstDetail] = DEMO_TICKET_DETAILS
      if (firstDetail !== undefined && url === `/v1/agent/tickets/${firstDetail.item.ticket_ref}`) {
        return Promise.resolve(jsonResponse(200, firstDetail))
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

  it('shows the real queue once the agent signs in', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText('Persona')
    await user.type(screen.getByLabelText('Access code'), 'agent-code')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    const [firstItem] = DEMO_QUEUE.items
    expect(firstItem).toBeDefined()
    expect(await screen.findByText(firstItem?.ticket_ref ?? '')).toBeInTheDocument()
  })

  it('shows a ticket’s real detail once its reference is clicked, then returns to the queue', async () => {
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

    const [firstDetail] = DEMO_TICKET_DETAILS
    expect(firstDetail).toBeDefined()
    expect(await screen.findByRole('region', { name: 'Detalle del ticket' })).toBeInTheDocument()
    expect(await screen.findByText(firstDetail?.packet.request_summary ?? '')).toBeInTheDocument()
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
