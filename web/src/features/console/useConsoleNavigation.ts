/**
 * `ConsoleApp`'s own navigation state: which agent session is active, and which ticket (if any)
 * is selected.
 *
 * `selectedTicketRef` is a sibling of `session`, not nested inside it, on purpose: a `session`
 * transition (a fresh sign-in, or a sign-out after expiry) never touches it, so when an agent
 * signs in again the selected ticket is still selected, by construction. It lives in its own hook
 * so that independence can be tested directly, without driving a sign-in flow.
 */
import { useState } from 'react'

/** The agent's signed-in session: the bearer token the clients send. */
export interface ConsoleSession {
  token: string
}

/** Navigation state and its setters; `null` means no session / no ticket selected. */
export interface ConsoleNavigation {
  session: ConsoleSession | null
  setSession: (session: ConsoleSession | null) => void
  selectedTicketRef: string | null
  setSelectedTicketRef: (ticketRef: string | null) => void
}

/** Holds the active session and the selected ticket reference as independent pieces of state. */
export function useConsoleNavigation(): ConsoleNavigation {
  const [session, setSession] = useState<ConsoleSession | null>(null)
  const [selectedTicketRef, setSelectedTicketRef] = useState<string | null>(null)

  return { session, setSession, selectedTicketRef, setSelectedTicketRef }
}
