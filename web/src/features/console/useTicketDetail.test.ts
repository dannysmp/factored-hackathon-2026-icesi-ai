/** Unit tests: the parts of `useTicketDetail`'s race guard a rendered UI can't reach directly. */
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
})
