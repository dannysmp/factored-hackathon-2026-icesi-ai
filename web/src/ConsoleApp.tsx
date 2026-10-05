import { useMemo, useState } from 'react'
import type { JSX } from 'react'
import { QueueScreen } from './features/console/QueueScreen'
import { TicketDetailScreen } from './features/console/TicketDetailScreen'
import { LiveQueueClient } from './features/console/client'
import { LiveTicketDetailClient } from './features/console/ticketDetailClient'
import { useConsoleNavigation } from './features/console/useConsoleNavigation'
import { SignInScreen } from './features/sign-in/SignInScreen'
import { Notice } from './components/ui/Notice'
import { PageHeader } from './components/ui/PageHeader'
import styles from './ConsoleApp.module.css'

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
 * The two live clients are built once per signed-in session, not on every render (frontend
 * standard, section 5): a new sign-in (a new token) is a new session in every sense, so a new
 * client for it is correct, not wasteful — the same rule the customer chat's own `LiveChatClient`
 * follows in `App.tsx`.
 *
 * A 401 from the queue or a ticket's detail means the session ended: it is not refreshed, so the
 * console returns to the sign-in with a notice. Only `session` is cleared; `selectedTicketRef`
 * stays, so the agent who signs in again lands back on the same ticket (AC-E10-08, AC-E10-16).
 *
 * The heading is fixed Spanish (D91: the console stays fixed-Spanish). `SignInScreen` reads its
 * copy from the trilingual `useT` hook, but only for the customer audience — passing
 * `audience="agent"` here keeps this screen's own sign-in fixed-Spanish too, regardless of which
 * agent persona is selected, matching the rest of the console.
 */
export function ConsoleApp(): JSX.Element {
  const { session, setSession, selectedTicketRef, setSelectedTicketRef } = useConsoleNavigation()

  const [expired, setExpired] = useState(false)
  const expireSession = (): void => {
    setExpired(true)
    setSession(null)
  }

  const queueClient = useMemo(
    () => (session === null ? null : new LiveQueueClient(session)),
    [session],
  )
  const ticketDetailClient = useMemo(
    () => (session === null ? null : new LiveTicketDetailClient(session)),
    [session],
  )

  if (session === null || queueClient === null || ticketDetailClient === null) {
    return (
      <>
        <PageHeader title="Consola del agente" width="wide" />
        <main>
          {expired ? (
            <div className={styles.notice}>
              <Notice tone="warning" role="status">
                Su sesión terminó. Inicie sesión de nuevo.
              </Notice>
            </div>
          ) : null}
          <SignInScreen
            audience="agent"
            onSignedIn={(token) => {
              setSession({ token })
            }}
          />
        </main>
      </>
    )
  }

  return (
    <>
      <PageHeader title="Consola del agente" width="wide" />
      <main>
        {selectedTicketRef === null ? (
          <QueueScreen
            client={queueClient}
            onSelectTicket={setSelectedTicketRef}
            onSessionExpired={expireSession}
          />
        ) : (
          <TicketDetailScreen
            client={ticketDetailClient}
            ticketRef={selectedTicketRef}
            onSessionExpired={expireSession}
            onBack={() => {
              setSelectedTicketRef(null)
            }}
          />
        )}
      </main>
    </>
  )
}
