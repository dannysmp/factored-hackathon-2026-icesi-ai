/** Component test: the console shell walks agent sign-in into the queue and a ticket's detail,
 * with no accessibility violations at any step. Every request (sign-in, queue, ticket detail) is
 * a mocked `fetch`, serving the same fixture data `web/src/features/console/fixtures.ts` already
 * validates against the real contracts. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsoleApp } from './ConsoleApp'
import { es } from './i18n/es'
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

function stubTheWholeFlow() {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
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
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConsoleApp', () => {
  it('shows the agent sign-in screen first, not the queue', async () => {
    stubTheWholeFlow()
    render(<ConsoleApp />)

    expect(await screen.findByLabelText(es['signin.personaLabel'])).toBeInTheDocument()
    expect(
      screen.queryByRole('region', { name: 'Cola de casos escalados' }),
    ).not.toBeInTheDocument()
  })

  it('shows the real queue once the agent signs in, fetched through the live route', async () => {
    const fetchMock = stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    const [firstItem] = DEMO_QUEUE.items
    expect(firstItem).toBeDefined()
    expect(await screen.findByText(firstItem?.ticket_ref ?? '')).toBeInTheDocument()

    const queueCall = fetchMock.mock.calls.find(([url]) => url === '/v1/agent/queue')
    if (queueCall === undefined) {
      throw new Error('expected the console to fetch /v1/agent/queue through LiveQueueClient')
    }
    const [, init] = queueCall
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer agent-token' })
  })

  it('shows a ticket’s real detail once its reference is clicked, fetched through the live route, then returns to the queue', async () => {
    const fetchMock = stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
    const rows = await screen.findAllByRole('row')
    const firstTicketRefButton = rows[1]?.querySelector('button')
    if (firstTicketRefButton === null || firstTicketRefButton === undefined) {
      throw new Error('expected the first row to contain a ticket reference button')
    }

    await user.click(firstTicketRefButton)

    const [firstDetail] = DEMO_TICKET_DETAILS
    if (firstDetail === undefined) {
      throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least one entry for this test')
    }
    expect(await screen.findByRole('region', { name: 'Detalle del ticket' })).toBeInTheDocument()
    expect(await screen.findByText(firstDetail.packet.request_summary)).toBeInTheDocument()
    expect(
      screen.queryByRole('region', { name: 'Cola de casos escalados' }),
    ).not.toBeInTheDocument()

    const detailCall = fetchMock.mock.calls.find(
      ([url]) => url === `/v1/agent/tickets/${firstDetail.item.ticket_ref}`,
    )
    if (detailCall === undefined) {
      throw new Error(
        'expected the console to fetch /v1/agent/tickets/{ref} through LiveTicketDetailClient',
      )
    }
    const [, init] = detailCall
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer agent-token' })

    await user.click(screen.getByRole('button', { name: 'Volver a la cola' }))

    expect(
      await screen.findByRole('region', { name: 'Cola de casos escalados' }),
    ).toBeInTheDocument()
  })

  it('shows a ticket’s detail once its reference is clicked, then returns to the queue', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
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

  it('names the document in Spanish and signs the agent out to the sign-in step', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    expect(document.documentElement.lang).toBe('es')
    expect(document.title).toBe('Consola del agente')
    expect(screen.queryByRole('button', { name: 'Cerrar sesión' })).not.toBeInTheDocument()

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
    await screen.findByRole('region', { name: 'Cola de casos escalados' })

    await user.click(screen.getByRole('button', { name: 'Cerrar sesión' }))

    expect(await screen.findByLabelText(es['signin.personaLabel'])).toBeInTheDocument()
    expect(
      screen.queryByRole('region', { name: 'Cola de casos escalados' }),
    ).not.toBeInTheDocument()
  })

  it('frames the sign-in step with a banner holding the console title and one main landmark', async () => {
    stubTheWholeFlow()
    render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    expect(screen.getAllByRole('banner')).toHaveLength(1)
    expect(screen.getByRole('banner')).toContainElement(
      screen.getByRole('heading', { level: 1, name: 'Consola del agente' }),
    )
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getAllByRole('main')).toHaveLength(1)
  })

  it('frames the queue and the ticket detail with the same banner, title and single main landmark', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
    await screen.findByRole('region', { name: 'Cola de casos escalados' })

    const expectFramed = (): void => {
      expect(screen.getAllByRole('banner')).toHaveLength(1)
      expect(screen.getByRole('banner')).toContainElement(
        screen.getByRole('heading', { level: 1, name: 'Consola del agente' }),
      )
      expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
      expect(screen.getAllByRole('main')).toHaveLength(1)
    }
    expectFramed()

    const [firstDetail] = DEMO_TICKET_DETAILS
    if (firstDetail === undefined) {
      throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least one entry for this test')
    }
    await user.click(screen.getByRole('button', { name: firstDetail.item.ticket_ref }))
    await screen.findByRole('region', { name: 'Detalle del ticket' })
    expectFramed()
  })

  it('has no automatically detectable accessibility violations at the sign-in step', async () => {
    stubTheWholeFlow()
    const { container } = render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations once the queue renders', async () => {
    stubTheWholeFlow()
    const user = userEvent.setup()
    const { container } = render(<ConsoleApp />)

    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
    await screen.findByRole('region', { name: 'Cola de casos escalados' })

    expect(await axe(container)).toHaveNoViolations()
  })
})
