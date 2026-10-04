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
  /** What was sent to the endpoint when that differs from the words shown, such as a list number. */
  sent?: string
  /** The customer's message may not have reached the assistant; `retry` sends it again. */
  failed?: boolean
  /** The id the turn endpoint treats as the identity of this message, so a resend cannot advance the conversation twice. */
  turnId?: string
}

export type ConversationStatus = 'loading' | 'ready' | 'error'

interface ConversationState {
  status: ConversationStatus
  messages: Message[]
  latest: TurnResponse | null
  error: string | null
}

export interface Conversation extends ConversationState {
  /**
   * Send the customer's text; a no-op once the conversation has ended or is already loading.
   * `shown` is what the transcript displays when that differs from what is sent, such as the
   * full description of a listed option that is sent as its number.
   *
   * A message that failed earlier is dropped from the transcript: the customer chose to say
   * something else, and it may not have reached the assistant.
   */
  send: (text: string, shown?: string) => void
  /** Send the message that failed again under its original id; a no-op when none failed. */
  retry: () => void
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

  // Set once, in the unmount cleanup below. `commit` checks it before every write, so a `start`
  // or `sendTurn` promise that settles after the component unmounted never calls `setState` on
  // it — one guard for every write, instead of repeating the check at each call site.
  const mounted = useRef(true)

  const commit = useCallback((next: ConversationState): void => {
    if (!mounted.current) return
    latestState.current = next
    setState(next)
  }, [])

  // If a caller ever swaps `client` for a genuinely different one (the fixture client today;
  // the live client is the next slice's), this effect re-runs and starts a new conversation, but
  // the previous one's messages linger until the new `start()` resolves. A caller that needs an
  // immediate reset should remount by changing this component's `key`, React's own tool for
  // that, rather than this hook resetting state itself from inside an effect.
  useEffect(() => {
    mounted.current = true
    client.start().then(
      (turn) => {
        commit({ status: 'ready', messages: [assistantMessage(turn)], latest: turn, error: null })
      },
      (error: unknown) => {
        commit({
          status: 'error',
          messages: [],
          latest: null,
          error: error instanceof Error ? error.message : 'the conversation could not start',
        })
      },
    )
    return () => {
      mounted.current = false
    }
  }, [client, commit])

  const dispatch = useCallback(
    (text: string, turnId: string, history: Message[]): void => {
      commit({ ...latestState.current, status: 'loading', messages: history })

      client.sendTurn(text, turnId).then(
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
            messages: before.messages.map((message) =>
              message.turnId === turnId ? { ...message, failed: true } : message,
            ),
            error: error instanceof Error ? error.message : 'the message could not be sent',
          })
        },
      )
    },
    [client, commit],
  )

  const send = useCallback(
    (text: string, shown?: string) => {
      const current = latestState.current
      if (current.status === 'loading' || current.latest?.end_session === true) {
        return
      }
      const turnId = crypto.randomUUID()
      const customerMessage: Message = {
        id: `customer-${turnId}`,
        from: 'customer',
        text: shown ?? text,
        ...(shown === undefined ? {} : { sent: text }),
        turnId,
      }
      dispatch(text, turnId, [
        ...current.messages.filter((m) => m.failed !== true),
        customerMessage,
      ])
    },
    [dispatch],
  )

  const retry = useCallback(() => {
    const current = latestState.current
    const failed = current.messages.find((message) => message.failed === true)
    if (failed?.turnId === undefined) {
      return
    }
    dispatch(
      failed.sent ?? failed.text,
      failed.turnId,
      current.messages.map((message) =>
        message === failed ? { ...message, failed: false } : message,
      ),
    )
  }, [dispatch])

  return { ...state, send, retry }
}
