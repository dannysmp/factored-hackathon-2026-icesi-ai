/** Unit tests: `fetchCustomerPersonas` and `signIn` against a mocked `fetch`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SignInError, fetchCustomerPersonas, signIn } from './api'

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

    expect(personas).toEqual([{ slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' }])
    expect(fetchMock).toHaveBeenCalledWith('/v1/auth/demo-personas')
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

describe('signIn', () => {
  it('posts the persona and access code, returning the access token', async () => {
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

  it('throws SignInError on a refusal, without leaking which check failed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { title: 'Sign-in was refused' })),
    )

    await expect(signIn('ana', 'wrong')).rejects.toBeInstanceOf(SignInError)
  })
})
