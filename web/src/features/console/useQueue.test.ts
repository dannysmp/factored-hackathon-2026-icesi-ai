/** Unit tests: the parts of `useQueue`'s race guard a rendered UI can't reach directly. */
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { QueueClient } from './client'
import type { QueueResponse } from './contracts'
import { useQueue } from './useQueue'

function response(referenceDate: string): QueueResponse {
  return { reference_date: referenceDate, reference_date_origin: 'setting', items: [] }
}

describe('useQueue', () => {
  it('commits only the most recently issued request, even if an earlier one resolves later', async () => {
    let resolveFirst: ((value: QueueResponse) => void) | undefined
    const client: QueueClient = {
      fetchQueue: (filters) => {
        if (filters.language === undefined) {
          return new Promise((resolve) => {
            resolveFirst = resolve
          })
        }
        return Promise.resolve(response('2026-06-19'))
      },
    }
    const { result } = renderHook(() => useQueue(client))

    act(() => {
      result.current.setLanguage('es')
    })
    await waitFor(() => {
      expect(result.current.referenceDate).toBe('2026-06-19')
    })

    act(() => {
      resolveFirst?.(response('stale'))
    })

    // Give the (already resolved) stale promise a turn to settle, then confirm it never landed.
    await Promise.resolve()
    expect(result.current.referenceDate).toBe('2026-06-19')
  })

  it('surfaces a rejection as the error state, with the queue empty', async () => {
    const client: QueueClient = {
      fetchQueue: () => Promise.reject(new Error('the queue service is down')),
    }
    const { result } = renderHook(() => useQueue(client))

    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })
    expect(result.current.error).toBe('the queue service is down')
    expect(result.current.items).toEqual([])
  })

  it('falls back to a generic message when a rejection carries no Error', async () => {
    // Deliberately a non-Error rejection: exercises the ternary's fallback branch for exactly
    // that case.
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    const client: QueueClient = { fetchQueue: () => Promise.reject('a plain string, not an Error') }
    const { result } = renderHook(() => useQueue(client))

    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })
    expect(result.current.error).toBe('the queue could not be loaded')
  })

  it('discards an earlier request that rejects after a later one already answered', async () => {
    let rejectFirst: ((error: unknown) => void) | undefined
    const client: QueueClient = {
      fetchQueue: (filters) => {
        if (filters.language === undefined) {
          return new Promise((_resolve, reject) => {
            rejectFirst = reject
          })
        }
        return Promise.resolve(response('2026-06-21'))
      },
    }
    const { result } = renderHook(() => useQueue(client))

    act(() => {
      result.current.setLanguage('es')
    })
    await waitFor(() => {
      expect(result.current.referenceDate).toBe('2026-06-21')
    })

    act(() => {
      rejectFirst?.(new Error('a stale request finally failed'))
    })

    // Give the (already stale) rejection a turn to settle, then confirm it never landed.
    await Promise.resolve()
    expect(result.current.status).toBe('ready')
    expect(result.current.error).toBeNull()
  })

  it('re-issues the request on retry', async () => {
    let calls = 0
    const client: QueueClient = {
      fetchQueue: () => {
        calls += 1
        return calls === 1
          ? Promise.reject(new Error('down'))
          : Promise.resolve(response('2026-06-20'))
      },
    }
    const { result } = renderHook(() => useQueue(client))
    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })

    act(() => {
      result.current.retry()
    })

    await waitFor(() => {
      expect(result.current.status).toBe('ready')
    })
    expect(result.current.referenceDate).toBe('2026-06-20')
    expect(calls).toBe(2)
  })
})
