/**
 * Drives one conversation against a `ChatClient`.
 *
 * Server state (the turns) lives here, not copied into components (frontend standard, section
 * 4): a component reads `messages`/`latest`/`status` and calls `send`, and never talks to the
 * client itself.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChatClient } from './client'
import type { TurnResponse } from './contracts'

export interface Message {
  id: string
  from: 'assistant' | 'customer'
  text: string
}

export type ConversationStatus = 'loading' | 'ready' | 'error'

interface ConversationState {
  status: ConversationStatus
  messages: Message[]
  latest: TurnResponse | null
  error: string | null
}

export interface Conversation extends ConversationState {
  /** Send the customer's text; a no-op once the conversation has ended or is already loading. */
  send: (text: string) => void
}

function assistantMessage(turn: TurnResponse): Message {
  return { id: turn.turn_id, from: 'assistant', text: turn.reply }
}

const INITIAL_STATE: ConversationState = {
  status: 'loading',
  messages: [],
  latest: null,
  error: null,
}

export function useConversation(client: ChatClient): Conversation {
  const [state, setState] = useState<ConversationState>(INITIAL_STATE)

  // `send`'s guard needs the true-right-now status to decide whether a click starts a request.
  // React does not guarantee a `setState` call's effect is visible to the very next line of the
  // handler that made it: a state update from an event handler and one from an earlier promise
  // callback can be flushed together, in either order. A ref written in the same handler that
  // calls `setState`, and read back in the same handler, has no such gap — it is an ordinary
  // synchronous assignment. Every transition below writes both, in the same order, every time.
  const latestState = useRef(state)

  function commit(next: ConversationState): void {
    latestState.current = next
    setState(next)
  }

  // If a caller ever swaps `client` for a genuinely different one (the fixture client today;
  // the live client is the next slice's), this effect re-runs and starts a new conversation, but
  // the previous one's messages linger until the new `start()` resolves. A caller that needs an
  // immediate reset should remount by changing this component's `key`, React's own tool for
  // that, rather than this hook resetting state itself from inside an effect.
  useEffect(() => {
    let cancelled = false
    client.start().then(
      (turn) => {
        if (cancelled) return
        commit({ status: 'ready', messages: [assistantMessage(turn)], latest: turn, error: null })
      },
      (error: unknown) => {
        if (cancelled) return
        commit({
          status: 'error',
          messages: [],
          latest: null,
          error: error instanceof Error ? error.message : 'the conversation could not start',
        })
      },
    )
    return () => {
      cancelled = true
    }
  }, [client])

  const send = useCallback(
    (text: string) => {
      const current = latestState.current
      if (current.status === 'loading' || current.latest?.end_session === true) {
        return
      }
      const customerMessage: Message = {
        id: `customer-${String(current.messages.length)}`,
        from: 'customer',
        text,
      }
      commit({ ...current, status: 'loading', messages: [...current.messages, customerMessage] })

      client.sendTurn(text).then(
        (turn) => {
          const before = latestState.current
          commit({
            ...before,
            status: 'ready',
            messages: [...before.messages, assistantMessage(turn)],
            latest: turn,
            error: null,
          })
        },
        (error: unknown) => {
          const before = latestState.current
          commit({
            ...before,
            status: 'error',
            error: error instanceof Error ? error.message : 'the message could not be sent',
          })
        },
      )
    },
    [client],
  )

  return { ...state, send }
}
