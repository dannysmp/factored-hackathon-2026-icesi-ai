import type { JSX } from 'react'
import { QueueScreen } from './features/console/QueueScreen'
import { TicketDetailScreen } from './features/console/TicketDetailScreen'
import { FixtureQueueClient } from './features/console/client'
import { FixtureTicketDetailClient } from './features/console/ticketDetailClient'
import { DEMO_QUEUE, DEMO_TICKET_DETAILS } from './features/console/fixtures'
import { useConsoleNavigation } from './features/console/useConsoleNavigation'
import { SignInScreen } from './features/sign-in/SignInScreen'

// Built once, at module scope: a client is a resource (frontend standard, section 5). The
// queue's and the ticket-detail's own read routes (`GET /v1/agent/queue`,
// `GET /v1/agent/tickets/{ticket_ref}`) are not wired into the running application yet (their
// shared router's `ConsoleAuditSink` dependency has no real implementation) — see
// `app/api/agent.py`'s own docstring — so a live client has nothing to call. These fixtures are
// the two screens' data source until that follow-up slice lands live clients alongside the live
// wiring; the session token below is not read by either yet for that same reason.
const queueClient = new FixtureQueueClient(DEMO_QUEUE)
const ticketDetailClient = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)

/**
 * The console shell: the agent demonstration sign-in (AC-E10-14, its own broker and access code,
 * `POST /v1/auth/demo-agent-sessions`), then the queue, then — once a ticket is selected — that
 * ticket's own detail. A separate entry point from the customer chat's `App.tsx`
 * (`console.html`/`console-main.tsx`) — two demo paths, not one app branching on a path a static
 * host would need a rewrite rule to serve.
 *
 * Navigation state (which session, which ticket) lives in `useConsoleNavigation`, not here,
 * specifically so `selectedTicketRef`'s independence from `session` (AC-E10-08) is directly unit
 * tested — see that hook's own docstring.
 *
 * The heading is fixed Spanish (D91: the console stays fixed-Spanish); `SignInScreen` itself is
 * not yet on the trilingual `useT` hook chat and sign-in are moving to (a separate, already-
 * assigned slice) — see Known Gaps.
 */
export function ConsoleApp(): JSX.Element {
  const { session, setSession, selectedTicketRef, setSelectedTicketRef } = useConsoleNavigation()

  if (session === null) {
    return (
      <main>
        <h1>Consola del agente</h1>
        <SignInScreen
          audience="agent"
          onSignedIn={(token) => {
            setSession({ token })
          }}
        />
      </main>
    )
  }

  return (
    <main>
      <h1>Consola del agente</h1>
      {selectedTicketRef === null ? (
        <QueueScreen client={queueClient} onSelectTicket={setSelectedTicketRef} />
      ) : (
        <TicketDetailScreen
          client={ticketDetailClient}
          ticketRef={selectedTicketRef}
          onBack={() => {
            setSelectedTicketRef(null)
          }}
        />
      )}
    </main>
  )
}
