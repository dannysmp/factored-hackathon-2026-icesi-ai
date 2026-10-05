/** The customer app shell: sign-in, then the live chat, with the session held in memory. */
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
 * The app shell: the demonstration sign-in, then the customer chat against the live turn
 * endpoint (`LiveChatClient`). The session token lives only in this component's own state, never
 * in browser storage, so closing the tab ends the session; the sign-in screen follows the same
 * held-in-memory rule.
 *
 * When the service reports the session has ended, the person is returned to the sign-in with a
 * note saying so, in the language the conversation was last in, and the keyboard lands on the
 * form. The same persona is offered again so signing back in takes one step.
 *
 * Both the sign-in and the chat render inside a `PageHeader` banner and a single `main` landmark.
 */
export function App(): JSX.Element {
  const [session, setSession] = useState<{ token: string; lang: Lang; slug: string } | null>(null)
  // The page's own language: the sign-in screen reports the language it is speaking from its first
  // render (the browser's, when it is one of the three, else Spanish) and then the selected
  // persona's, and the chat reports the conversation's.
  const [lang, setLang] = useState<Lang>('es')
  // The language the ended session was in, while its note is on screen above the sign-in.
  const [expiredIn, setExpiredIn] = useState<Lang | null>(null)
  // The persona whose session ended, so the sign-in offers the same person again.
  const [expiredSlug, setExpiredSlug] = useState<string | undefined>(undefined)
  const t = useT(lang)
  const tExpired = useT(expiredIn ?? lang)
  useDocumentLanguage(lang, t('app.title'))

  // Built once per signed-in session, not on every render: a client is a resource. A new sign-in
  // (a new token) is a new session in every sense, so a new client for it is correct, not wasteful.
  const client = useMemo(() => (session === null ? null : new LiveChatClient(session)), [session])

  const handleExpired = useCallback(() => {
    setExpiredIn(lang)
    setExpiredSlug(session?.slug)
    setSession(null)
  }, [lang, session])

  if (session === null || client === null) {
    return (
      <>
        <PageHeader title={t('app.title')} width="form" />
        <main>
          {expiredIn !== null && (
            <div className={styles.notice} lang={expiredIn}>
              <Notice tone="warning" role="status">
                {tExpired('app.sessionExpired')}
              </Notice>
            </div>
          )}
          <SignInScreen
            focusForm={expiredIn !== null}
            preferredLang={expiredIn ?? undefined}
            preferredSlug={expiredSlug}
            onSignedIn={(token, signedInLang, slug) => {
              setExpiredIn(null)
              setExpiredSlug(undefined)
              setSession({ token, lang: signedInLang, slug })
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
