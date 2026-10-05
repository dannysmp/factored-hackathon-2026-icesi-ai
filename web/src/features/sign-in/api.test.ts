/** Unit tests: `fetchCustomerPersonas`, `fetchAgentPersonas` and `signIn` against a mocked
 * `fetch`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SignInError, fetchAgentPersonas, fetchCustomerPersonas, signIn } from './api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchCustomerPersonas', () => {
  it('returns only the customer personas, dropping any agent ones', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        personas: [
          { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' },
          { slug: 'agent-diego', display_name: 'Diego', language: 'es', audience: 'agent' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const personas = await fetchCustomerPersonas()

    expect(personas).toEqual([
      { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' },
    ])
    expect(fetchMock).toHaveBeenCalledWith('/v1/auth/demo-personas', {
      signal: expect.any(AbortSignal) as AbortSignal,
    })
  })

  it('throws SignInError with the problem title on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(429, { title: 'Too many attempts' })),
    )

    await expect(fetchCustomerPersonas()).rejects.toMatchObject({
      name: 'SignInError',
      status: 429,
      message: 'Too many attempts',
    })
  })
})

describe('fetchAgentPersonas', () => {
  it('returns only the agent personas, dropping any customer ones', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        personas: [
          { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' },
          { slug: 'diego', display_name: 'Diego', language: 'pt', audience: 'agent' },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const personas = await fetchAgentPersonas()

    expect(personas).toEqual([
      { slug: 'diego', display_name: 'Diego', language: 'pt', audience: 'agent' },
    ])
    expect(fetchMock).toHaveBeenCalledWith('/v1/auth/demo-personas', {
      signal: expect.any(AbortSignal) as AbortSignal,
    })
  })
})

describe('signIn', () => {
  it('posts the persona and access code to the customer broker by default', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        access_token: 'token-123',
        token_type: 'Bearer',
        expires_at: '2026-09-28T12:30:00Z',
        expires_in: 1800,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const token = await signIn('ana', 'the-code')

    expect(token).toBe('token-123')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/v1/auth/demo-sessions')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'X-Demo-Access-Code': 'the-code' })
    expect(JSON.parse(init.body as string)).toEqual({ persona: 'ana' })
  })

  it("posts to the agent broker's own path when audience is 'agent'", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        access_token: 'token-456',
        token_type: 'Bearer',
        expires_at: '2026-09-28T13:30:00Z',
        expires_in: 3600,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const token = await signIn('diego', 'agent-code', 'agent')

    expect(token).toBe('token-456')
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/v1/auth/demo-agent-sessions')
  })

  it('throws SignInError on a refusal, without leaking which check failed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { title: 'Sign-in was refused' })),
    )

    await expect(signIn('ana', 'wrong')).rejects.toBeInstanceOf(SignInError)
  })
})
