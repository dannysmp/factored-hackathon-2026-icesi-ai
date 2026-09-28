/** Unit tests: `FixtureChatClient` replays its script in order and validates every turn;
 * `LiveChatClient` posts real turns against a mocked `fetch`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChatClient } from './client'
import { FixtureChatClient, LiveChatClient, ScriptExhaustedError, TurnRequestError } from './client'
import { FILE_DISPUTE_EN } from './fixtures'
import type { TurnResponse } from './contracts'

describe('FixtureChatClient', () => {
  it('replays the script in order from start', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN)
    const first = await client.start()
    expect(first.turn_id).toBe('fixture-turn-0001')
    const second = await client.sendTurn('anything')
    expect(second.turn_id).toBe('fixture-turn-0002')
  })

  it('rejects once every scripted turn has been played', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN.slice(0, 1))
    await client.start()
    await expect(client.sendTurn('anything')).rejects.toThrow(ScriptExhaustedError)
  })

  it('restarts from the first turn when start is called again', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN)
    await client.start()
    await client.sendTurn('anything')
    const restarted = await client.start()
    expect(restarted.turn_id).toBe('fixture-turn-0001')
  })

  it('rejects a script whose turns do not match the real contract', () => {
    expect(() => new FixtureChatClient([{ reply: 'missing every other required field' }])).toThrow()
  })
})

function turnResponse(overrides: Partial<TurnResponse> = {}): TurnResponse {
  return {
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
    ...overrides,
  }
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('LiveChatClient', () => {
  it('sends the language-appropriate greeting word as the opening turn', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, turnResponse()))
    vi.stubGlobal('fetch', fetchMock)
    const client: ChatClient = new LiveChatClient({ token: 'tok', lang: 'es' })

    await client.start()

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/v1/turns')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok' })
    expect(JSON.parse(init.body as string).text).toBe('Hola')
  })

  it('uses a fresh turn id for every call, never reusing one', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(200, turnResponse())))
    vi.stubGlobal('fetch', fetchMock)
    const client: ChatClient = new LiveChatClient({ token: 'tok', lang: 'en' })

    await client.start()
    await client.sendTurn('a real message')

    const firstId = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string)
      .turn_id
    const secondId = JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string)
      .turn_id
    expect(firstId).not.toBe(secondId)
    expect(firstId).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  })

  it('sends the customer text verbatim on sendTurn, not the greeting word', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, turnResponse()))
    vi.stubGlobal('fetch', fetchMock)
    const client: ChatClient = new LiveChatClient({ token: 'tok', lang: 'en' })

    await client.sendTurn('I have a problem with a charge')

    const body = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string)
    expect(body.text).toBe('I have a problem with a charge')
  })

  it('parses a real response against the turn contract', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, turnResponse({ reply: 'A grounded reply' }))),
    )
    const client: ChatClient = new LiveChatClient({ token: 'tok', lang: 'es' })

    const turn = await client.start()

    expect(turn.reply).toBe('A grounded reply')
  })

  it('rejects with TurnRequestError carrying the problem document title on a refusal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(409, { title: 'The conversation moved on' })),
    )
    const client: ChatClient = new LiveChatClient({ token: 'tok', lang: 'es' })

    const error = await client.sendTurn('anything').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(TurnRequestError)
    expect((error as TurnRequestError).status).toBe(409)
    expect((error as TurnRequestError).message).toBe('The conversation moved on')
  })
})
