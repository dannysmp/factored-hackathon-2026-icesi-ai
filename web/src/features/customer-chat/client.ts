/**
 * Chat client: the one seam between the UI and a turn source.
 *
 * Components never call a transport directly (frontend standard, section 5: "one HTTP client
 * module"). `FixtureChatClient` replays a script; `LiveChatClient` is the real HTTP client against
 * the turn endpoint, behind a demo session.
 */
import type { Lang, TurnRequest, TurnResponse } from './contracts'
import { TurnResponseSchema } from './contracts'
import { requestSignal } from '../../lib/failure'

const TURNS_PATH = '/v1/turns'

/** The word `_SMALL_TALK` (app/conversation/understanding.py) recognizes, one per language. */
const GREETING_TRIGGER: Record<Lang, string> = { es: 'Hola', pt: 'Olá', en: 'Hello' }

export interface ChatClient {
  /** The assistant's opening message, before the customer has said anything. */
  start: () => Promise<TurnResponse>
  /**
   * Send the customer's text and get the next turn. A caller that may resend the same message
   * passes its own `turnId`, so the server recognises the repeat instead of advancing twice.
   */
  sendTurn: (text: string, turnId?: string) => Promise<TurnResponse>
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

/** Raised when the turn endpoint refuses a request; `message` is the problem document's own
 * title, safe to show a customer (never the raw response body, which may carry detail meant
 * for logs only). */
export class TurnRequestError extends Error {
  readonly status: number

  constructor(status: number, title: string) {
    super(title)
    this.name = 'TurnRequestError'
    this.status = status
  }
}

/**
 * The real chat client, against `POST /v1/turns` behind a demo session token.
 *
 * `start()` makes no request of its own: nothing exists on the server for a session with no
 * turns yet, and the turn contract has no "before anything happened" shape (`state_version` is
 * bounded at 1 or above, `reference_date_line` is never blank). Instead it sends the one
 * language-appropriate word `_SMALL_TALK` recognizes (app/conversation/understanding.py) as a
 * real first turn, and returns the assistant's real greeting reply — the customer never sees
 * their own triggering text, exactly like `FixtureChatClient`'s first scripted line, but this one
 * is the server's own grounded output. Every turn after that carries the client-chosen id the
 * contract requires (`crypto.randomUUID()`), so a retried request cannot advance the conversation
 * twice.
 */
export class LiveChatClient implements ChatClient {
  private readonly token: string
  private readonly lang: Lang

  constructor({ token, lang }: { token: string; lang: Lang }) {
    this.token = token
    this.lang = lang
  }

  start(): Promise<TurnResponse> {
    return this.postTurn(GREETING_TRIGGER[this.lang])
  }

  sendTurn(text: string, turnId?: string): Promise<TurnResponse> {
    return this.postTurn(text, turnId)
  }

  private async postTurn(
    text: string,
    turnId: string = crypto.randomUUID(),
  ): Promise<TurnResponse> {
    const body: TurnRequest = { turn_id: turnId, text }
    const response = await fetch(TURNS_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.token}` },
      body: JSON.stringify(body),
      signal: requestSignal(),
    })
    if (!response.ok) {
      throw await this.toError(response)
    }
    return TurnResponseSchema.parse(await response.json())
  }

  private async toError(response: Response): Promise<TurnRequestError> {
    try {
      const problem: unknown = await response.json()
      const title =
        typeof problem === 'object' && problem !== null && 'title' in problem
          ? String(problem.title)
          : response.statusText
      return new TurnRequestError(response.status, title)
    } catch {
      return new TurnRequestError(response.status, response.statusText || 'the request failed')
    }
  }
}
