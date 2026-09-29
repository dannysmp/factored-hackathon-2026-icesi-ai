/**
 * Drives the ticket-detail screen against a `TicketDetailClient`.
 *
 * Server state (the packet and the timeline) lives here, not copied into components (frontend
 * standard, section 4, the same rule `useConversation`/`useQueue` follow): a component reads
 * `status`/`detail`/`error` and calls `retry`, and never talks to the client itself.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { TicketDetailClient } from './ticketDetailClient'
import type { TicketDetail } from './contracts'

export type TicketDetailStatus = 'loading' | 'ready' | 'not_found' | 'error'

interface TicketDetailState {
  status: TicketDetailStatus
  detail: TicketDetail | null
  error: string | null
}

export interface TicketDetailQuery extends TicketDetailState {
  /** Re-issues the current request; the error state's own recovery action. */
  retry: () => void
}

const INITIAL_STATE: TicketDetailState = { status: 'loading', detail: null, error: null }

export function useTicketDetail(client: TicketDetailClient, ticketRef: string): TicketDetailQuery {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<TicketDetailState>(INITIAL_STATE)

  const mounted = useRef(true)
  // `ticketRef` or a retry can change while a fetch is in flight; only the most recently issued
  // request's response is committed, matching `useQueue`'s own guard.
  const requestId = useRef(0)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    const thisRequest = (requestId.current += 1)
    client.fetchTicketDetail(ticketRef).then(
      (detail) => {
        if (!mounted.current || requestId.current !== thisRequest) return
        setState({ status: detail === null ? 'not_found' : 'ready', detail, error: null })
      },
      (error: unknown) => {
        if (!mounted.current || requestId.current !== thisRequest) return
        setState({
          status: 'error',
          detail: null,
          error: error instanceof Error ? error.message : 'the ticket could not be loaded',
        })
      },
    )
  }, [client, ticketRef, attempt])

  const retry = useCallback(() => {
    setState((current) => ({ ...current, status: 'loading', error: null }))
    setAttempt((current) => current + 1)
  }, [])

  return { ...state, retry }
}
