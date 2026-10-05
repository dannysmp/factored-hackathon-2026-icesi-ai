/**
 * Demo sign-in: the one seam between the sign-in screen and the broker (ADR-18).
 *
 * Both calls are plain `fetch`, matching `customer-chat/client.ts`'s `LiveChatClient` — no shared
 * HTTP wrapper exists yet, and the two features hit different, small enough surfaces that one
 * would be a premature abstraction.
 */
import type { DemoPersonaSummary } from './contracts'
import { DemoPersonaDirectorySchema, SessionResponseSchema } from './contracts'
import { requestSignal } from '../../lib/failure'

const DEMO_PERSONAS_PATH = '/v1/auth/demo-personas'
const DEMO_SESSIONS_PATH = '/v1/auth/demo-sessions'
const DEMO_AGENT_SESSIONS_PATH = '/v1/auth/demo-agent-sessions'

/** `SignInAudience` (app/security/signin_audit.py): which broker and access code a sign-in
 * uses. `signIn` defaults to `'customer'`, matching every call site before the console existed. */
export type SignInAudience = 'customer' | 'agent'

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

async function fetchPersonas(audience: SignInAudience): Promise<readonly DemoPersonaSummary[]> {
  const response = await fetch(DEMO_PERSONAS_PATH, { signal: requestSignal() })
  if (!response.ok) {
    throw await toError(response)
  }
  const directory = DemoPersonaDirectorySchema.parse(await response.json())
  return directory.personas.filter((persona) => persona.audience === audience)
}

/** The customer personas the demo broker currently accepts. */
export function fetchCustomerPersonas(): Promise<readonly DemoPersonaSummary[]> {
  return fetchPersonas('customer')
}

/** The agent personas the demo broker currently accepts — the console's own sign-in. */
export function fetchAgentPersonas(): Promise<readonly DemoPersonaSummary[]> {
  return fetchPersonas('agent')
}

/** Claims a demo session for `persona` against `audience`'s own broker and access code
 * (`DEMO_SESSIONS_PATH` for `'customer'`, `DEMO_AGENT_SESSIONS_PATH` for `'agent'` — ADR-18: "a
 * leaked customer code leaves the console protected"), or throws `SignInError` (a wrong code and
 * an unknown persona are refused identically by either broker; this client does not try to tell
 * them apart either). */
export async function signIn(
  persona: string,
  accessCode: string,
  audience: SignInAudience = 'customer',
): Promise<string> {
  const path = audience === 'agent' ? DEMO_AGENT_SESSIONS_PATH : DEMO_SESSIONS_PATH
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Demo-Access-Code': accessCode },
    body: JSON.stringify({ persona }),
    signal: requestSignal(),
  })
  if (!response.ok) {
    throw await toError(response)
  }
  const session = SessionResponseSchema.parse(await response.json())
  return session.access_token
}
