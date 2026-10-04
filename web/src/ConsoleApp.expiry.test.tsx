/** Component test: a session the backend ends returns the agent to sign-in, keeping the ticket. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsoleApp } from './ConsoleApp'
import { es } from './i18n/es'
import { DEMO_QUEUE, DEMO_TICKET_DETAILS } from './features/console/fixtures'

const SESSIONS_PATH = '/v1/auth/demo-agent-sessions'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

function sessionBody(token: string): unknown {
  return {
    access_token: token,
    token_type: 'Bearer',
    expires_at: '2026-09-28T13:00:00Z',
    expires_in: 3600,
  }
}

interface Call {
  url: string
  authorization: string | null
}

function stubBackend(
  route: (url: string, token: string | null, signIns: number) => Response,
): Call[] {
  const calls: Call[] = []
  let signIns = 0
  vi.stubGlobal(
    'fetch',
    vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url, init) => {
      const headers = new Headers(init?.headers)
      calls.push({ url, authorization: headers.get('Authorization') })
      if (url === '/v1/auth/demo-personas') {
        return Promise.resolve(
          jsonResponse(200, {
            personas: [{ slug: 'diego', display_name: 'Diego', language: 'es', audience: 'agent' }],
          }),
        )
      }
      if (url === SESSIONS_PATH) {
        signIns += 1
        return Promise.resolve(jsonResponse(201, sessionBody(`agent-token-${String(signIns)}`)))
      }
      return Promise.resolve(route(url, headers.get('Authorization'), signIns))
    }),
  )
  return calls
}

async function signIn(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await screen.findByLabelText(es['signin.personaLabel'])
  await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
  await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConsoleApp session expiry', () => {
  it('returns to the sign-in, with a notice and no retry, when the queue answers 401', async () => {
    const calls = stubBackend((url, _token, signIns) =>
      url.startsWith('/v1/agent/queue') && signIns === 1
        ? jsonResponse(401, { title: 'Session expired', status: 401 })
        : jsonResponse(200, DEMO_QUEUE),
    )
    const user = userEvent.setup()
    render(<ConsoleApp />)
    await signIn(user)

    expect(await screen.findByLabelText(es['signin.accessCodeLabel'])).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Su sesión terminó')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(calls.filter((call) => call.url === SESSIONS_PATH)).toHaveLength(1)

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    const [firstItem] = DEMO_QUEUE.items
    expect(await screen.findByText(firstItem?.ticket_ref ?? '')).toBeInTheDocument()
    expect(screen.queryByText('Su sesión terminó. Inicie sesión de nuevo.')).not.toBeInTheDocument()
  })

  it('keeps a failure that is not a session ending as a retryable error', async () => {
    stubBackend((url) =>
      url.startsWith('/v1/agent/queue')
        ? jsonResponse(500, { title: 'Internal error', status: 500 })
        : jsonResponse(200, DEMO_QUEUE),
    )
    const user = userEvent.setup()
    render(<ConsoleApp />)
    await signIn(user)

    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo cargar la cola')
    expect(screen.getByRole('button', { name: 'Intentar de nuevo' })).toBeInTheDocument()
    expect(screen.queryByLabelText(es['signin.accessCodeLabel'])).not.toBeInTheDocument()
  })

  it('returns to the sign-in from a ticket and shows the same ticket after a new sign-in', async () => {
    const [detail] = DEMO_TICKET_DETAILS
    if (detail === undefined) throw new Error('the fixtures hold no ticket detail')
    const ticketRef = detail.item.ticket_ref
    const calls = stubBackend((url, token) => {
      if (url.startsWith('/v1/agent/queue')) return jsonResponse(200, DEMO_QUEUE)
      if (url.includes(encodeURIComponent(ticketRef))) {
        return token === 'Bearer agent-token-1'
          ? jsonResponse(401, { title: 'Session expired', status: 401 })
          : jsonResponse(200, detail)
      }
      return jsonResponse(404, { title: 'Not found', status: 404 })
    })
    const user = userEvent.setup()
    render(<ConsoleApp />)
    await signIn(user)
    await user.click(await screen.findByText(ticketRef))

    expect(await screen.findByLabelText(es['signin.accessCodeLabel'])).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Su sesión terminó')

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    expect(await screen.findByRole('heading', { name: ticketRef })).toBeInTheDocument()
    const ticketCalls = calls.filter((call) => call.url.includes(encodeURIComponent(ticketRef)))
    expect(ticketCalls.map((call) => call.authorization)).toEqual([
      'Bearer agent-token-1',
      'Bearer agent-token-2',
    ])
  })
})
