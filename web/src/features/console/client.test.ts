/** Unit tests: `FixtureQueueClient` replays and filters a queue snapshot; `LiveQueueClient` calls
 * the real route against a mocked `fetch`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentRequestError, FixtureQueueClient, LiveQueueClient } from './client'
import { DEMO_QUEUE, EMPTY_QUEUE } from './fixtures'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FixtureQueueClient', () => {
  it('answers the whole queue when no filter is given', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({})

    expect(response.items).toHaveLength(DEMO_QUEUE.items.length)
    expect(response.reference_date).toBe(DEMO_QUEUE.reference_date)
  })

  it('filters by language', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({ language: 'pt' })

    expect(response.items.every((item) => item.language === 'pt')).toBe(true)
    expect(response.items.length).toBeGreaterThan(0)
  })

  it('filters by trigger', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({ trigger: 'fraud_report' })

    expect(response.items.every((item) => item.trigger === 'fraud_report')).toBe(true)
    expect(response.items.length).toBeGreaterThan(0)
  })

  it('composes both filters', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({ language: 'pt', trigger: 'fraud_report' })

    expect(response.items).toEqual([])
  })

  it('answers an empty queue with no items', async () => {
    const client = new FixtureQueueClient(EMPTY_QUEUE)

    const response = await client.fetchQueue({})

    expect(response.items).toEqual([])
  })

  it('rejects a snapshot that does not match the contract', () => {
    expect(() => new FixtureQueueClient({ items: [{ ticket_ref: 'x' }] })).toThrow()
  })
})

describe('LiveQueueClient', () => {
  it('sends the bearer token and no query string when no filter is given', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, DEMO_QUEUE))
    vi.stubGlobal('fetch', fetchMock)
    const client = new LiveQueueClient({ token: 'agent-token' })

    const response = await client.fetchQueue({})

    expect(response.items).toHaveLength(DEMO_QUEUE.items.length)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/v1/agent/queue')
    expect(init.headers).toMatchObject({ Authorization: 'Bearer agent-token' })
  })

  it('sends language and trigger as query parameters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, DEMO_QUEUE))
    vi.stubGlobal('fetch', fetchMock)
    const client = new LiveQueueClient({ token: 'agent-token' })

    await client.fetchQueue({ language: 'pt', trigger: 'fraud_report' })

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/v1/agent/queue?language=pt&trigger=fraud_report')
  })

  it('throws AgentRequestError with the problem title on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { title: 'Sign in again to continue.' })),
    )
    const client = new LiveQueueClient({ token: 'expired' })

    await expect(client.fetchQueue({})).rejects.toMatchObject({
      name: 'AgentRequestError',
      status: 401,
      message: 'Sign in again to continue.',
    })
    await expect(client.fetchQueue({})).rejects.toBeInstanceOf(AgentRequestError)
  })
})
