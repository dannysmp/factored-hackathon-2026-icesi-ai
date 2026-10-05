import { useCallback, useMemo, useState } from 'react'
import type { JSX } from 'react'
import { ChatFeature } from './features/customer-chat/ChatFeature'
import { LiveChatClient } from './features/customer-chat/client'
import type { Lang } from './features/customer-chat/contracts'
import { SignInScreen } from './features/sign-in/SignInScreen'
import { Button } from './components/ui/Button'
import { Notice } from './components/ui/Notice'
import { PageHeader } from './components/ui/PageHeader'
import { useDocumentLanguage } from './i18n/useDocumentLanguage'
import { useT } from './i18n/useT'
import styles from './App.module.css'

/**
 * The app shell: the demonstration sign-in, then the customer chat against the real, live turn
 * endpoint (ADR-18, `LiveChatClient`). The session token lives only in this component's own
 * state, never storage — the same "held in memory only" rule the sign-in screen itself follows.
 *
 * When the service reports the session has ended, the person is returned to the sign-in with a
 * note saying so, in the language they were using, and the keyboard lands on the form.
 */
export function App(): JSX.Element {
  const [session, setSession] = useState<{ token: string; lang: Lang } | null>(null)
  // The page's own language: the sign-in screen reports the selected persona's, then the chat
  // reports the conversation's. Spanish, the product's first language, until either has spoken.
  const [lang, setLang] = useState<Lang>('es')
  // The language the ended session was in, while its note is on screen above the sign-in.
  const [expiredIn, setExpiredIn] = useState<Lang | null>(null)
  const t = useT(lang)
  const tExpired = useT(expiredIn ?? lang)
  useDocumentLanguage(lang, t('app.title'))

  // Built once per signed-in session, not on every render (frontend standard, section 3): a
  // client is a resource. A new sign-in (a new token) is a new session in every sense, so a new
  // client for it is correct, not wasteful.
  const client = useMemo(() => (session === null ? null : new LiveChatClient(session)), [session])

  const handleExpired = useCallback(() => {
    setExpiredIn(session?.lang ?? null)
    setSession(null)
  }, [session])

  if (session === null || client === null) {
    return (
      <>
        <PageHeader title={t('app.title')} />
        <main>
          {expiredIn !== null && (
            <div className={styles.notice}>
              <Notice tone="warning" role="status">
                {tExpired('app.sessionExpired')}
              </Notice>
            </div>
          )}
          <SignInScreen
            focusForm={expiredIn !== null}
            preferredLang={expiredIn ?? undefined}
            onSignedIn={(token, signedInLang) => {
              setExpiredIn(null)
              setSession({ token, lang: signedInLang })
            }}
            onLanguageChange={setLang}
          />
        </main>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t('app.title')}>
        <Button
          variant="quiet"
          onClick={() => {
            setSession(null)
          }}
        >
          {t('app.signOut')}
        </Button>
      </PageHeader>
      <main>
        <ChatFeature
          client={client}
          lang={session.lang}
          onLanguageChange={setLang}
          onSessionExpired={handleExpired}
        />
      </main>
    </>
  )
}
