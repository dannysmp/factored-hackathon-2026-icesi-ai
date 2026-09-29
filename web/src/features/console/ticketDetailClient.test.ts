/** Unit tests: `FixtureTicketDetailClient` answers by `ticket_ref`, `null` for an unknown one;
 * `LiveTicketDetailClient` calls the real route against a mocked `fetch`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentRequestError } from './client'
import { FixtureTicketDetailClient, LiveTicketDetailClient } from './ticketDetailClient'
import { DEMO_TICKET_DETAILS } from './fixtures'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FixtureTicketDetailClient', () => {
  it('answers a known ticket by its own reference', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    const [first] = DEMO_TICKET_DETAILS
    expect(first).toBeDefined()

    const detail = await client.fetchTicketDetail(first?.item.ticket_ref ?? '')

    expect(detail?.item.ticket_ref).toBe(first?.item.ticket_ref)
  })

  it('answers null for an unknown reference, never an error', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)

    const detail = await client.fetchTicketDetail('T-NOT-A-REAL-TICKET')

    expect(detail).toBeNull()
  })

  it('rejects a snapshot that does not match the contract', () => {
    expect(() => new FixtureTicketDetailClient([{ item: { ticket_ref: 'x' } }])).toThrow()
  })
})

describe('LiveTicketDetailClient', () => {
  it('sends the bearer token and returns the parsed detail', async () => {
    const [detail] = DEMO_TICKET_DETAILS
    if (detail === undefined) {
      throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least one entry for this test')
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, detail))
    vi.stubGlobal('fetch', fetchMock)
    const client = new LiveTicketDetailClient({ token: 'agent-token' })

    const result = await client.fetchTicketDetail(detail.item.ticket_ref)

    expect(result?.item.ticket_ref).toBe(detail.item.ticket_ref)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`/v1/agent/tickets/${detail.item.ticket_ref}`)
    expect(init.headers).toMatchObject({ Authorization: 'Bearer agent-token' })
  })

  it('answers null on a 404, matching the fixture client and the real route', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(404, { title: 'Not found' })))
    const client = new LiveTicketDetailClient({ token: 'agent-token' })

    const result = await client.fetchTicketDetail('T-NOT-A-REAL-TICKET')

    expect(result).toBeNull()
  })

  it('throws AgentRequestError on any other non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { title: 'Sign in again to continue.' })),
    )
    const client = new LiveTicketDetailClient({ token: 'expired' })

    await expect(client.fetchTicketDetail('T-ANY')).rejects.toBeInstanceOf(AgentRequestError)
  })
})
