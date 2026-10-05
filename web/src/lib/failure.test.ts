/** Unit tests: a failed request is sorted into the kind of problem a person can act on. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { REQUEST_TIMEOUT_MS, classifyFailure, requestSignal } from './failure'

describe('classifyFailure', () => {
  it.each([
    [401, 'unauthorized'],
    [429, 'rateLimited'],
    [500, 'unavailable'],
    [503, 'unavailable'],
    [404, 'other'],
    [409, 'other'],
  ] as const)('reads status %i as %s', (status, kind) => {
    expect(classifyFailure({ status })).toBe(kind)
  })

  it('reads a request that timed out as a timeout', () => {
    expect(classifyFailure(new DOMException('timed out', 'TimeoutError'))).toBe('timeout')
  })

  it('does not read any other abort as a timeout', () => {
    expect(classifyFailure(new DOMException('cancelled', 'AbortError'))).toBe('other')
  })

  it('reads a request that never reached the service as offline', () => {
    expect(classifyFailure(new TypeError('Failed to fetch'))).toBe('offline')
  })

  it('prefers the status over the shape of the error', () => {
    const error = Object.assign(new TypeError('odd'), { status: 429 })
    expect(classifyFailure(error)).toBe('rateLimited')
  })

  it.each([[new Error('boom')], [{ status: 'nope' }], [null], ['text']])(
    'reads %o as other',
    (error) => {
      expect(classifyFailure(error)).toBe('other')
    },
  )
})

describe('requestSignal', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('gives a signal that is not yet aborted', () => {
    expect(requestSignal().aborted).toBe(false)
  })

  it('times a request out after the request limit', () => {
    const timeout = vi.spyOn(AbortSignal, 'timeout')

    requestSignal()

    expect(REQUEST_TIMEOUT_MS).toBe(30_000)
    expect(timeout).toHaveBeenCalledWith(REQUEST_TIMEOUT_MS)
  })
})
