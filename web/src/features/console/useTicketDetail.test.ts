/**
 * Unit tests: `useTicketDetail`'s load states (not found, error, retry), its guard that commits
 * only the latest request's response, and its 401 handling, which a rendered UI cannot reach
 * directly.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentRequestError } from './client'
import type { TicketDetailClient } from './ticketDetailClient'
import type { TicketDetail } from './contracts'
import { DEMO_TICKET_DETAILS } from './fixtures'
import { useTicketDetail } from './useTicketDetail'

const [FIRST_DETAIL, SECOND_DETAIL] = DEMO_TICKET_DETAILS

describe('useTicketDetail', () => {
  it('answers not_found for a ticket_ref the client has nothing for', async () => {
    const client: TicketDetailClient = { fetchTicketDetail: () => Promise.resolve(null) }
    const { result } = renderHook(() => useTicketDetail(client, 'T-UNKNOWN'))

    await waitFor(() => {
      expect(result.current.status).toBe('not_found')
    })
    expect(result.current.detail).toBeNull()
  })

  it('surfaces a rejection as the error state', async () => {
    const client: TicketDetailClient = {
      fetchTicketDetail: () => Promise.reject(new Error('the ticket service is down')),
    }
    const { result } = renderHook(() => useTicketDetail(client, 'T-ANY'))

    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })
    expect(result.current.error).toBe('the ticket service is down')
  })

  it('falls back to a generic message when a rejection carries no Error', async () => {
    const client: TicketDetailClient = {
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      fetchTicketDetail: () => Promise.reject('a plain string, not an Error'),
    }
    const { result } = renderHook(() => useTicketDetail(client, 'T-ANY'))

    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })
    expect(result.current.error).toBe('the ticket could not be loaded')
  })

  it('re-issues the request on retry', async () => {
    let calls = 0
    const client: TicketDetailClient = {
      fetchTicketDetail: () => {
        calls += 1
        return calls === 1
          ? Promise.reject(new Error('down'))
          : Promise.resolve(FIRST_DETAIL ?? null)
      },
    }
    const { result } = renderHook(() =>
      useTicketDetail(client, FIRST_DETAIL?.item.ticket_ref ?? ''),
    )
    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })

    act(() => {
      result.current.retry()
    })

    await waitFor(() => {
      expect(result.current.status).toBe('ready')
    })
    expect(calls).toBe(2)
  })

  it('commits only the most recently issued request, even if an earlier one resolves later', async () => {
    let resolveFirst: ((value: TicketDetail | null) => void) | undefined
    const client: TicketDetailClient = {
      fetchTicketDetail: (ticketRef) => {
        if (ticketRef === FIRST_DETAIL?.item.ticket_ref) {
          return new Promise((resolve) => {
            resolveFirst = resolve
          })
        }
        return Promise.resolve(SECOND_DETAIL ?? null)
      },
    }
    const { result, rerender } = renderHook(
      ({ ticketRef }: { ticketRef: string }) => useTicketDetail(client, ticketRef),
      { initialProps: { ticketRef: FIRST_DETAIL?.item.ticket_ref ?? '' } },
    )

    rerender({ ticketRef: SECOND_DETAIL?.item.ticket_ref ?? '' })
    await waitFor(() => {
      expect(result.current.detail?.item.ticket_ref).toBe(SECOND_DETAIL?.item.ticket_ref)
    })

    act(() => {
      resolveFirst?.(FIRST_DETAIL ?? null)
    })

    await Promise.resolve()
    expect(result.current.detail?.item.ticket_ref).toBe(SECOND_DETAIL?.item.ticket_ref)
  })

  describe('when the backend answers 401', () => {
    const expired = (): Promise<TicketDetail | null> =>
      Promise.reject(new AgentRequestError(401, 'Session expired'))

    it('calls onSessionExpired a single time and does not enter the error state', async () => {
      const onSessionExpired = vi.fn()
      const client: TicketDetailClient = { fetchTicketDetail: expired }
      const { result } = renderHook(() => useTicketDetail(client, 'T-ANY', onSessionExpired))

      await waitFor(() => {
        expect(onSessionExpired).toHaveBeenCalledTimes(1)
      })
      expect(result.current.status).toBe('loading')
    })

    it('stays a retryable error when no handler is given', async () => {
      const client: TicketDetailClient = { fetchTicketDetail: expired }
      const { result } = renderHook(() => useTicketDetail(client, 'T-ANY'))

      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })
    })

    it('does not call it for another status', async () => {
      const onSessionExpired = vi.fn()
      const client: TicketDetailClient = {
        fetchTicketDetail: () => Promise.reject(new AgentRequestError(500, 'Internal error')),
      }
      const { result } = renderHook(() => useTicketDetail(client, 'T-ANY', onSessionExpired))

      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })
      expect(onSessionExpired).not.toHaveBeenCalled()
    })

    it('does not call it after unmount', async () => {
      const onSessionExpired = vi.fn()
      let rejectRequest: (reason: Error) => void = () => undefined
      const client: TicketDetailClient = {
        fetchTicketDetail: () =>
          new Promise<TicketDetail | null>((_resolve, reject) => {
            rejectRequest = reject
          }),
      }
      const { unmount } = renderHook(() => useTicketDetail(client, 'T-ANY', onSessionExpired))
      unmount()

      await act(async () => {
        rejectRequest(new AgentRequestError(401, 'Session expired'))
        await Promise.resolve()
      })

      expect(onSessionExpired).not.toHaveBeenCalled()
    })

    it('ignores a superseded request and honours the current one', async () => {
      const onSessionExpired = vi.fn()
      const rejections: ((reason: Error) => void)[] = []
      const client: TicketDetailClient = {
        fetchTicketDetail: () =>
          new Promise<TicketDetail | null>((_resolve, reject) => {
            rejections.push(reject)
          }),
      }
      const { rerender } = renderHook(({ ref }) => useTicketDetail(client, ref, onSessionExpired), {
        initialProps: { ref: 'T-ONE' },
      })
      rerender({ ref: 'T-TWO' })
      await waitFor(() => {
        expect(rejections).toHaveLength(2)
      })

      await act(async () => {
        rejections[0]?.(new AgentRequestError(401, 'Session expired'))
        await Promise.resolve()
      })
      expect(onSessionExpired).not.toHaveBeenCalled()

      await act(async () => {
        rejections[1]?.(new AgentRequestError(401, 'Session expired'))
        await Promise.resolve()
      })
      expect(onSessionExpired).toHaveBeenCalledTimes(1)
    })
  })
})
