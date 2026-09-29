/**
 * Drives the queue screen against a `QueueClient`.
 *
 * Server state (the queue) lives here, not copied into components (frontend standard, section
 * 4, the same rule `useConversation` follows): a component reads `status`/`items`/`language` and
 * calls `setLanguage`/`retry`, and never talks to the client itself.
 *
 * The trigger view (all/priority/other, `QueueFilters.tsx`) is not a fetch parameter: it is a
 * pure view over whatever `items` already holds, since `QueueItem.priority` is already on every
 * row and the queue is not paginated. Only `language` is a real filter the backend contract
 * accepts (`QueueFilters.language`, `contracts/service_v1/console.py`), so only it is threaded
 * through to `fetchQueue`.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Lang } from '../customer-chat/contracts'
import type { QueueClient } from './client'
import type { QueueItem } from './contracts'

export type QueueStatus = 'loading' | 'ready' | 'error'

interface QueueState {
  status: QueueStatus
  items: readonly QueueItem[]
  referenceDate: string | null
  error: string | null
}

export interface Queue extends QueueState {
  language: Lang | undefined
  /** `undefined` clears the filter (every language). */
  setLanguage: (language: Lang | undefined) => void
  /** Re-issues the current request; the error state's own recovery action. */
  retry: () => void
}

const INITIAL_STATE: QueueState = {
  status: 'loading',
  items: [],
  referenceDate: null,
  error: null,
}

export function useQueue(client: QueueClient): Queue {
  const [language, setLanguageState] = useState<Lang | undefined>(undefined)
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<QueueState>(INITIAL_STATE)

  const mounted = useRef(true)
  // Filters (or a retry) can change while a fetch is in flight; only the most recently issued
  // request's response is committed, so a slow response to a stale combination never overwrites
  // a newer one that already answered.
  const requestId = useRef(0)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  // The effect only starts the fetch and commits its outcome; it never calls `setState`
  // synchronously in its own body (react-hooks/set-state-in-effect) — the "now loading" state
  // for a filter change or a retry is set in the event handler that causes it, below. The very
  // first fetch needs no such call either: `INITIAL_STATE.status` is already `'loading'`.
  useEffect(() => {
    const thisRequest = (requestId.current += 1)
    client.fetchQueue({ language }).then(
      (response) => {
        if (!mounted.current || requestId.current !== thisRequest) return
        setState({
          status: 'ready',
          items: response.items,
          referenceDate: response.reference_date,
          error: null,
        })
      },
      (error: unknown) => {
        if (!mounted.current || requestId.current !== thisRequest) return
        setState((current) => ({
          ...current,
          status: 'error',
          error: error instanceof Error ? error.message : 'the queue could not be loaded',
        }))
      },
    )
  }, [client, language, attempt])

  const setLanguage = useCallback((next: Lang | undefined) => {
    setState((current) => ({ ...current, status: 'loading', error: null }))
    setLanguageState(next)
  }, [])

  const retry = useCallback(() => {
    setState((current) => ({ ...current, status: 'loading', error: null }))
    setAttempt((current) => current + 1)
  }, [])

  return { ...state, language, setLanguage, retry }
}
