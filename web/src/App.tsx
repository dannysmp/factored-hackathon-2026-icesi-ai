import { useMemo, useState } from 'react'
import type { JSX } from 'react'
import { ChatFeature } from './features/customer-chat/ChatFeature'
import { LiveChatClient } from './features/customer-chat/client'
import type { Lang } from './features/customer-chat/contracts'
import { SignInScreen } from './features/sign-in/SignInScreen'
import styles from './App.module.css'

/**
 * The app shell: the demonstration sign-in, then the customer chat against the real, live turn
 * endpoint (ADR-18, `LiveChatClient`). The session token lives only in this component's own
 * state, never storage — the same "held in memory only" rule the sign-in screen itself follows.
 */
export function App(): JSX.Element {
  const [session, setSession] = useState<{ token: string; lang: Lang } | null>(null)

  // Built once per signed-in session, not on every render (frontend standard, section 3): a
  // client is a resource. A new sign-in (a new token) is a new session in every sense, so a new
  // client for it is correct, not wasteful.
  const client = useMemo(() => (session === null ? null : new LiveChatClient(session)), [session])

  if (session === null || client === null) {
    return (
      <main>
        <h1 className={styles.heading}>Dispute intake</h1>
        <SignInScreen
          onSignedIn={(token, lang) => {
            setSession({ token, lang })
          }}
        />
      </main>
    )
  }

  return (
    <main>
      <h1 className={styles.heading}>Dispute intake</h1>
      <ChatFeature client={client} />
    </main>
  )
}
