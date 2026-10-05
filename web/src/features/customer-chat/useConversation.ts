/**
 * Drives one conversation against a `ChatClient`.
 *
 * Server state (the turns) lives here, not copied into components: a component reads
 * `messages`/`latest`/`status` and calls `send`, and never talks to the client itself.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChatClient } from './client'
import type { TurnResponse } from './contracts'
import { classifyFailure } from '../../lib/failure'
import type { FailureKind } from '../../lib/failure'

/** One line of the transcript, from the assistant or the customer. */
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

/** `loading` while a request is in flight, `error` after one failed, `ready` otherwise. */
export type ConversationStatus = 'loading' | 'ready' | 'error'

/** Everything the hook keeps; `latest` is the newest assistant turn, `null` before the first. */
interface ConversationState {
  status: ConversationStatus
  messages: Message[]
  latest: TurnResponse | null
  /** The case number of the dispute filed in this conversation, kept after the turn that carried it; `null` until one is filed. */
  filedCase: string | null
  /** What went wrong while the status is `error`; `null` otherwise. */
  failure: FailureKind | null
}

/** What a component sees of a conversation: its state plus the two actions it may take. */
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
  /**
   * Try again after a failure: resends the message that failed under its original id, or, when
   * the conversation never started, asks for the opening message again. A no-op otherwise.
   */
  retry: () => void
}

/** The transcript line for an assistant turn, keyed by the turn's own id. */
function assistantMessage(turn: TurnResponse): Message {
  return { id: turn.turn_id, from: 'assistant', text: turn.reply }
}

/** The state before the opening message arrives, and after a failed opening is retried. */
const INITIAL_STATE: ConversationState = {
  status: 'loading',
  messages: [],
  latest: null,
  filedCase: null,
  failure: null,
}

/**
 * Runs a conversation against `client`: requests the opening message on mount, then one request
 * per `send`. Responses that arrive after unmount are discarded. A failed send keeps the message
 * in the transcript, marked `failed`, so `retry` can resend it under the same turn id.
 */
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

  const begin = useCallback((): void => {
    client.start().then(
      (turn) => {
        commit({
          status: 'ready',
          messages: [assistantMessage(turn)],
          latest: turn,
          filedCase: turn.case_number,
          failure: null,
        })
      },
      (error: unknown) => {
        commit({
          status: 'error',
          messages: [],
          latest: null,
          filedCase: null,
          failure: classifyFailure(error),
        })
      },
    )
  }, [client, commit])

  // If a caller ever swaps `client` for a genuinely different one, this effect re-runs and starts
  // a new conversation, but the previous one's messages linger until the new `start()` resolves.
  // A caller that needs an immediate reset should remount by changing this component's `key`,
  // React's own tool for that, rather than this hook resetting state itself from inside an effect.
  useEffect(() => {
    mounted.current = true
    begin()
    return () => {
      mounted.current = false
    }
  }, [begin])

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
            filedCase: turn.case_number ?? before.filedCase,
            failure: null,
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
            failure: classifyFailure(error),
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
    if (current.status === 'error' && current.latest === null) {
      commit({ ...INITIAL_STATE })
      begin()
      return
    }
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
  }, [begin, commit, dispatch])

  return { ...state, send, retry }
}
