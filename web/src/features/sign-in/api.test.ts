/** Unit tests: `fetchCustomerPersonas`, `fetchAgentPersonas` and `signIn` against a mocked
 * `fetch`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SignInError, endSession, fetchAgentPersonas, fetchCustomerPersonas, signIn } from './api'
import { REFERENCE_DATE_LINES } from './personaDirectory'

/** A JSON `Response` with the given status and body, like the broker returns. */
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
        reference_date_lines: REFERENCE_DATE_LINES,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const directory = await fetchCustomerPersonas()

    expect(directory.personas).toEqual([
      { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' },
    ])
    expect(directory.referenceDateLines).toEqual(REFERENCE_DATE_LINES)
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

describe('the wait a refusal asks for', () => {
  /** A refusal whose `Retry-After` header is `value` (or has none). */
  function refusal(value?: string): Response {
    return new Response(JSON.stringify({ title: 'Too many attempts' }), {
      status: 429,
      headers: {
        'Content-Type': 'application/json',
        ...(value === undefined ? {} : { 'Retry-After': value }),
      },
    })
  }

  it.each([
    ['58', 58],
    [' 3 ', 3],
    [undefined, null],
    ['0', null],
    ['-4', null],
    ['1.5', null],
    ['Wed, 21 Oct 2026 07:28:00 GMT', null],
  ])('reads Retry-After %j as %j seconds', async (header, seconds) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(refusal(header)))

    await expect(fetchCustomerPersonas()).rejects.toMatchObject({
      status: 429,
      retryAfterSeconds: seconds,
    })
  })

  it('keeps the wait when the refusal has no readable body', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response('not json', { status: 429, headers: { 'Retry-After': '7' } }),
        ),
    )

    await expect(fetchCustomerPersonas()).rejects.toMatchObject({ retryAfterSeconds: 7 })
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
        reference_date_lines: REFERENCE_DATE_LINES,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const directory = await fetchAgentPersonas()

    expect(directory.personas).toEqual([
      { slug: 'diego', display_name: 'Diego', language: 'pt', audience: 'agent' },
    ])
    expect(directory.referenceDateLines).toEqual(REFERENCE_DATE_LINES)
    expect(fetchMock).toHaveBeenCalledWith('/v1/auth/demo-personas', {
      signal: expect.any(AbortSignal) as AbortSignal,
    })
  })
})

describe('a directory the service sent without its reference date', () => {
  it('is refused rather than shown without the date', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(200, {
          personas: [{ slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' }],
        }),
      ),
    )

    await expect(fetchCustomerPersonas()).rejects.toThrow()
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

describe('signIn when the profile is held by another session', () => {
  it('carries the problem code, so the screen can tell it from a rate limit', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(409, { title: 'This profile is in use', code: 'demo_persona_in_use' }),
        ),
    )

    await expect(signIn('ana', 'code')).rejects.toMatchObject({
      name: 'SignInError',
      status: 409,
      code: 'demo_persona_in_use',
    })
  })

  it('has no code when the answer carries none', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 502 })))

    await expect(signIn('ana', 'code')).rejects.toMatchObject({ status: 502, code: null })
  })
})

describe('endSession', () => {
  it.each([
    ['customer', '/v1/auth/logout'],
    ['agent', '/v1/agent/auth/logout'],
  ] as const)('posts the %s token to %s', async (audience, path) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await endSession('the-token', audience)

    expect(fetchMock).toHaveBeenCalledWith(path, {
      method: 'POST',
      headers: { Authorization: 'Bearer the-token' },
      keepalive: true,
      signal: expect.any(AbortSignal) as AbortSignal,
    })
  })

  it('never throws, whatever the service does', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(endSession('the-token', 'customer')).resolves.toBeUndefined()
  })
})
