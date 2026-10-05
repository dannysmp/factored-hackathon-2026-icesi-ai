import { useEffect, useId, useState } from 'react'
import type { JSX, SyntheticEvent } from 'react'
import type { DemoPersonaSummary } from './contracts'
import type { Lang } from '../customer-chat/contracts'
import type { SignInAudience } from './api'
import { Button } from '../../components/ui/Button'
import { SignInError, fetchAgentPersonas, fetchCustomerPersonas, signIn } from './api'
import { useT } from '../../i18n/useT'
import { LANGUAGES } from '../../i18n/lang'
import styles from './SignInScreen.module.css'

type DirectoryStatus = 'loading' | 'ready' | 'unavailable' | 'error'

/** Before any persona is selected (loading, the directory error), nothing has told this screen
 * which language to speak in yet — Spanish is the product's own first-listed, required language
 * (CLAUDE.md), so it is this screen's own starting point, not a guess. */
const DEFAULT_LANG: Lang = 'es'

/** `DemoPersonaSummary.language` is a bare, unvalidated string at the wire contract (it mirrors
 * the backend's own persona model, which is not scoped to this frontend's three display
 * languages) — falls back to `DEFAULT_LANG` rather than crash on a value `useT`'s catalog lookup
 * doesn't recognize. */
function toLang(value: string): Lang {
  return (LANGUAGES as readonly string[]).includes(value) ? (value as Lang) : DEFAULT_LANG
}

/**
 * The demo sign-in screen (ADR-18): a persona picker built from the real, live persona directory
 * (never a hardcoded copy — `GET /v1/auth/demo-personas`) plus the access code, distributed
 * out-of-band to whoever runs the demonstration, never baked into this bundle.
 *
 * `audience` (AC-E10-14: "the screen asks for the access code of its own audience") selects the
 * persona list and the broker this screen signs into — `'customer'`, the only caller before the
 * console existed, is the default so every earlier call site is unchanged.
 *
 * When the sign-in is switched off (AC-E10-15: the kill switch) the backend either answers the
 * persona directory with 401 `session_missing` (both brokers off, so the route is not public) or
 * lists no persona for this audience (only the other broker on); either way the screen says
 * plainly that the demonstration is not available and offers no form. A directory that fails for
 * any other reason (a network error, a rate limit, a server error) stays the retryable
 * "unreachable" state. No persona is selected in the unavailable state, so its text is always the
 * default language's; the Portuguese and English catalog entries exist for catalog parity only.
 *
 * `onSignedIn` receives the session token and the chosen persona's language, so the caller can
 * hand both to `LiveChatClient` — the token is this component's own state, held only for the
 * moment it takes to pass it up; nothing here ever writes it to storage (ADR-18: "the token held
 * in memory only").
 *
 * This screen's own copy follows the selected persona's language for the customer audience
 * (D91); the agent audience stays fixed-Spanish regardless of persona, matching the rest of the
 * console (`ConsoleApp.tsx`). Before a persona is selected, `DEFAULT_LANG` covers the loading
 * and directory-error states, which occur before any language signal exists.
 */
export function SignInScreen({
  audience = 'customer',
  onSignedIn,
}: {
  audience?: SignInAudience
  onSignedIn: (token: string, lang: Lang) => void
}): JSX.Element {
  const [directoryStatus, setDirectoryStatus] = useState<DirectoryStatus>('loading')
  const [personas, setPersonas] = useState<readonly DemoPersonaSummary[]>([])
  const [selectedSlug, setSelectedSlug] = useState('')
  const [accessCode, setAccessCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [signInError, setSignInError] = useState<string | null>(null)
  const personaFieldId = useId()
  const accessCodeFieldId = useId()
  const selectedPersona = personas.find((candidate) => candidate.slug === selectedSlug)
  // The console stays fixed-Spanish regardless of which agent persona is selected (D91); only
  // the customer path follows the selected persona's own language.
  const t = useT(
    audience === 'agent'
      ? DEFAULT_LANG
      : selectedPersona !== undefined
        ? toLang(selectedPersona.language)
        : DEFAULT_LANG,
  )

  useEffect(() => {
    let cancelled = false
    const fetchPersonas = audience === 'agent' ? fetchAgentPersonas : fetchCustomerPersonas
    fetchPersonas().then(
      (fetched) => {
        if (cancelled) return
        setPersonas(fetched)
        setSelectedSlug(fetched[0]?.slug ?? '')
        setDirectoryStatus(fetched.length === 0 ? 'unavailable' : 'ready')
      },
      (error: unknown) => {
        if (cancelled) return
        const switchedOff = error instanceof SignInError && error.status === 401
        setDirectoryStatus(switchedOff ? 'unavailable' : 'error')
      },
    )
    return () => {
      cancelled = true
    }
  }, [audience])

  function handleSubmit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (selectedPersona === undefined || accessCode.trim() === '' || submitting) {
      return
    }
    setSubmitting(true)
    setSignInError(null)
    signIn(selectedPersona.slug, accessCode, audience).then(
      (token) => {
        onSignedIn(token, toLang(selectedPersona.language))
      },
      () => {
        setSubmitting(false)
        setSignInError(t('signin.refused'))
      },
    )
  }

  if (directoryStatus === 'loading') {
    return (
      <p className={styles.status} aria-live="polite" role="status">
        {t('signin.loading')}
      </p>
    )
  }

  if (directoryStatus === 'unavailable') {
    return (
      <p className={styles.status} role="status">
        {t('signin.unavailable')}
      </p>
    )
  }

  if (directoryStatus === 'error') {
    return (
      <div role="alert" className={styles.error}>
        <p>{t('signin.unreachable')}</p>
      </div>
    )
  }

  return (
    <section aria-label="Demonstration sign-in" className={styles.screen}>
      <p className={styles.intro}>{t('signin.intro')}</p>
      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor={personaFieldId}>
            {t('signin.personaLabel')}
          </label>
          <select
            id={personaFieldId}
            className={styles.select}
            value={selectedSlug}
            disabled={submitting}
            onChange={(event) => {
              setSelectedSlug(event.target.value)
            }}
          >
            {personas.map((persona) => (
              <option key={persona.slug} value={persona.slug}>
                {persona.display_name}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor={accessCodeFieldId}>
            {t('signin.accessCodeLabel')}
          </label>
          <input
            id={accessCodeFieldId}
            type="password"
            className={styles.accessCode}
            value={accessCode}
            disabled={submitting}
            onChange={(event) => {
              setAccessCode(event.target.value)
            }}
          />
        </div>

        {signInError !== null && (
          <p role="alert" className={styles.formError}>
            {signInError}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          large
          disabled={submitting || selectedSlug === '' || accessCode === ''}
        >
          {t('signin.submit')}
        </Button>
      </form>
    </section>
  )
}
