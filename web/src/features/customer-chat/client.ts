/**
 * Chat client: the one seam between the UI and a turn source.
 *
 * Components never call a transport directly (frontend standard, section 5: "one HTTP client
 * module"). `FixtureChatClient` is the only implementation this slice ships; a live HTTP client
 * against the real turn endpoint is the next slice's, gated on the demonstration sign-in broker
 * and the conversation store (1.5a, 2.4) — this interface is the seam it will implement.
 */
import type { TurnResponse } from './contracts'
import { TurnResponseSchema } from './contracts'

export interface ChatClient {
  /** The assistant's opening message, before the customer has said anything. */
  start: () => Promise<TurnResponse>
  /** Send the customer's text and get the next turn. */
  sendTurn: (text: string) => Promise<TurnResponse>
}

/** Raised when a fixture script has no more turns, or a caller sends text after it ended. */
export class ScriptExhaustedError extends Error {
  constructor() {
    super('the scripted conversation has no more turns')
    this.name = 'ScriptExhaustedError'
  }
}

/**
 * Replays a fixed, ordered list of turns, validating each one against the real contract.
 *
 * It does not read the customer's text to decide what comes next: a fixture is a script, not a
 * model, and pretending otherwise would test the UI against a scenario that cannot happen from
 * the real endpoint.
 */
export class FixtureChatClient implements ChatClient {
  private readonly turns: readonly TurnResponse[]
  private cursor = 0

  constructor(turns: readonly unknown[]) {
    this.turns = turns.map((turn) => TurnResponseSchema.parse(turn))
  }

  start(): Promise<TurnResponse> {
    this.cursor = 0
    return this.next()
  }

  // A script does not read the customer's text, so it takes none: TypeScript allows an
  // implementation to drop trailing parameters the interface declares but this one never needs.
  sendTurn(): Promise<TurnResponse> {
    return this.next()
  }

  private next(): Promise<TurnResponse> {
    const turn = this.turns[this.cursor]
    if (turn === undefined) {
      return Promise.reject(new ScriptExhaustedError())
    }
    this.cursor += 1
    return Promise.resolve(turn)
  }
}
