import { useState } from 'react'
import type { JSX } from 'react'
import { QueueScreen } from './features/console/QueueScreen'
import { FixtureQueueClient } from './features/console/client'
import { DEMO_QUEUE } from './features/console/fixtures'
import { SignInScreen } from './features/sign-in/SignInScreen'

// Built once, at module scope: a client is a resource (frontend standard, section 5). The
// queue's own read route (`GET /v1/agent/queue`) is not wired into the running application yet
// (its `ConsoleAuditSink` dependency has no real implementation) — see `app/api/agent.py`'s own
// docstring — so a live client has nothing to call. This fixture is the queue screen's data
// source until that follow-up slice lands a `LiveQueueClient` alongside the live wiring; the
// session token below is not read by it yet for that same reason.
const client = new FixtureQueueClient(DEMO_QUEUE)

/**
 * The console shell: the agent demonstration sign-in (AC-E10-14, its own broker and access code,
 * `POST /v1/auth/demo-agent-sessions`), then the queue. A separate entry point from the customer
 * chat's `App.tsx` (`console.html`/`console-main.tsx`) — two demo paths, not one app branching on
 * a path a static host would need a rewrite rule to serve.
 *
 * The heading is fixed Spanish (D91: the console stays fixed-Spanish); `SignInScreen` itself is
 * not yet on the trilingual `useT` hook chat and sign-in are moving to (a separate, already-
 * assigned slice) — see Known Gaps.
 */
export function ConsoleApp(): JSX.Element {
  const [session, setSession] = useState<{ token: string } | null>(null)

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
      <QueueScreen client={client} />
    </main>
  )
}
