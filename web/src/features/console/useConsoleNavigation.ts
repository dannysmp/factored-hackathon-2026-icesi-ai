/**
 * `ConsoleApp`'s own navigation state: which agent session is active, and which ticket (if any)
 * is selected.
 *
 * `selectedTicketRef` is a sibling of `session`, not nested inside it, on purpose: a `session`
 * transition (a fresh sign-in, and eventually an expiry-triggered sign-out — neither mechanism
 * exists yet anywhere in `web/`) never touches it, so AC-E10-08 ("the agent signs in again... the
 * selected ticket is still selected") holds by construction. Extracted from `ConsoleApp` into its
 * own hook specifically so that property is directly testable (`useConsoleNavigation.test.ts`)
 * without needing to drive a full sign-in flow through a mocked `fetch` for a test that is really
 * about state independence, not about signing in.
 */
import { useState } from 'react'

export interface ConsoleSession {
  token: string
}

export interface ConsoleNavigation {
  session: ConsoleSession | null
  setSession: (session: ConsoleSession | null) => void
  selectedTicketRef: string | null
  setSelectedTicketRef: (ticketRef: string | null) => void
}

export function useConsoleNavigation(): ConsoleNavigation {
  const [session, setSession] = useState<ConsoleSession | null>(null)
  const [selectedTicketRef, setSelectedTicketRef] = useState<string | null>(null)

  return { session, setSession, selectedTicketRef, setSelectedTicketRef }
}
