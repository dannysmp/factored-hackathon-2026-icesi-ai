/** Component test: a console session the backend no longer accepts shows a handled state. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsoleApp } from './ConsoleApp'
import { es } from './i18n/es'
import { DEMO_QUEUE } from './features/console/fixtures'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConsoleApp session expiry', () => {
  it('shows the retryable error, not a blank screen, when the queue refuses the expired token', async () => {
    let queueCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn<(url: string) => Promise<Response>>((url) => {
        if (url === '/v1/auth/demo-personas') {
          return Promise.resolve(
            jsonResponse(200, {
              personas: [
                { slug: 'diego', display_name: 'Diego', language: 'es', audience: 'agent' },
              ],
            }),
          )
        }
        if (url === '/v1/auth/demo-agent-sessions') {
          return Promise.resolve(
            jsonResponse(201, {
              access_token: 'agent-token',
              token_type: 'Bearer',
              expires_at: '2026-09-28T13:00:00Z',
              expires_in: 3600,
            }),
          )
        }
        queueCalls += 1
        return Promise.resolve(
          queueCalls === 1
            ? jsonResponse(401, { title: 'Session expired', status: 401 })
            : jsonResponse(200, DEMO_QUEUE),
        )
      }),
    )
    const user = userEvent.setup()
    render(<ConsoleApp />)
    await screen.findByLabelText(es['signin.personaLabel'])
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'agent-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('No se pudo cargar la cola')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Intentar de nuevo' }))

    const [firstItem] = DEMO_QUEUE.items
    expect(await screen.findByText(firstItem?.ticket_ref ?? '')).toBeInTheDocument()
  })
})
