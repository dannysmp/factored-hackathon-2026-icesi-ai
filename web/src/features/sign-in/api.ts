/**
 * Demo sign-in: the one seam between the sign-in screen and the broker (ADR-18).
 *
 * Both calls are plain `fetch`, matching `customer-chat/client.ts`'s `LiveChatClient` — no shared
 * HTTP wrapper exists yet, and the two features hit different, small enough surfaces that one
 * would be a premature abstraction.
 */
import type { DemoPersonaSummary } from './contracts'
import { DemoPersonaDirectorySchema, SessionResponseSchema } from './contracts'

const DEMO_PERSONAS_PATH = '/v1/auth/demo-personas'
const DEMO_SESSIONS_PATH = '/v1/auth/demo-sessions'

/** Raised when the persona list or the sign-in itself cannot be fetched. */
export class SignInError extends Error {
  readonly status: number

  constructor(status: number, title: string) {
    super(title)
    this.name = 'SignInError'
    this.status = status
  }
}

async function toError(response: Response): Promise<SignInError> {
  try {
    const problem: unknown = await response.json()
    const title =
      typeof problem === 'object' && problem !== null && 'title' in problem
        ? String(problem.title)
        : response.statusText
    return new SignInError(response.status, title)
  } catch {
    return new SignInError(response.status, response.statusText || 'the request failed')
  }
}

/** The customer personas the demo broker currently accepts (agent personas never reach this
 * screen — the web app is the customer chat only). */
export async function fetchCustomerPersonas(): Promise<readonly DemoPersonaSummary[]> {
  const response = await fetch(DEMO_PERSONAS_PATH)
  if (!response.ok) {
    throw await toError(response)
  }
  const directory = DemoPersonaDirectorySchema.parse(await response.json())
  return directory.personas.filter((persona) => persona.audience === 'customer')
}

/** Claims a demo session for `persona`, or throws `SignInError` (a wrong code and an unknown
 * persona are refused identically by the broker, ADR-18 — this client does not try to tell them
 * apart either). */
export async function signIn(persona: string, accessCode: string): Promise<string> {
  const response = await fetch(DEMO_SESSIONS_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Demo-Access-Code': accessCode },
    body: JSON.stringify({ persona }),
  })
  if (!response.ok) {
    throw await toError(response)
  }
  const session = SessionResponseSchema.parse(await response.json())
  return session.access_token
}
