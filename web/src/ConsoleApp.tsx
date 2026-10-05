/** The agent console shell: agent sign-in, then the ticket queue, then one ticket's detail. */
import { useMemo, useState } from 'react'
import type { JSX } from 'react'
import { QueueScreen } from './features/console/QueueScreen'
import { TicketDetailScreen } from './features/console/TicketDetailScreen'
import { LiveQueueClient } from './features/console/client'
import { LiveTicketDetailClient } from './features/console/ticketDetailClient'
import { useConsoleNavigation } from './features/console/useConsoleNavigation'
import { SignInScreen } from './features/sign-in/SignInScreen'
import { Notice } from './components/ui/Notice'
import { Button } from './components/ui/Button'
import { PageHeader } from './components/ui/PageHeader'
import { useDocumentLanguage } from './i18n/useDocumentLanguage'
import styles from './ConsoleApp.module.css'

/** The console is written in Spanish only, so its page title and document language are fixed. */
const CONSOLE_TITLE = 'Consola del agente'

/**
 * The console shell: the agent demonstration sign-in (its own broker and access code,
 * `POST /v1/auth/demo-agent-sessions`), then the queue, then, once a ticket is selected, that
 * ticket's own detail. It is a separate entry point from the customer chat's `App.tsx`
 * (`console.html` / `console-main.tsx`): two demo paths, not one app branching on a URL path
 * that a static host would need a rewrite rule to serve.
 *
 * Navigation state (which session, which ticket) lives in `useConsoleNavigation`, not here, so
 * that the selected ticket's independence from the session can be unit tested on its own.
 *
 * The two live clients are built once per signed-in session, not on every render: a new sign-in
 * (a new token) is a new session in every sense, so a new client for it is correct, not wasteful.
 * The customer chat's `LiveChatClient` follows the same rule in `App.tsx`.
 *
 * A 401 from the queue or a ticket's detail means the session ended: it is not refreshed, so the
 * console returns to the sign-in with a notice. Only `session` is cleared; `selectedTicketRef`
 * stays, so the agent who signs in again lands back on the same ticket. Signing out by choice
 * clears both, so the next agent starts at the queue.
 *
 * The console stays in Spanish whatever the agent persona's language. `SignInScreen` reads its
 * copy from the trilingual `useT` hook only for the customer audience, so passing
 * `audience="agent"` keeps this sign-in in Spanish too, matching the rest of the console.
 */
export function ConsoleApp(): JSX.Element {
  const { session, setSession, selectedTicketRef, setSelectedTicketRef } = useConsoleNavigation()

  const [expired, setExpired] = useState(false)
  const expireSession = (): void => {
    setExpired(true)
    setSession(null)
  }
  const signOut = (): void => {
    setExpired(false)
    setSelectedTicketRef(null)
    setSession(null)
  }
  useDocumentLanguage('es', CONSOLE_TITLE)

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
        <PageHeader title={CONSOLE_TITLE} width="wide" />
        <main>
          {expired ? (
            <div className={styles.notice}>
              <Notice tone="warning" role="status">
                Su sesión expiró. Inicie sesión de nuevo.
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
      <PageHeader title={CONSOLE_TITLE} width="wide">
        <Button variant="quiet" onClick={signOut}>
          Cerrar sesión
        </Button>
      </PageHeader>
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
