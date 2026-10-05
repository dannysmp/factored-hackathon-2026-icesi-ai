import { useEffect, useId, useRef, useState } from 'react'
import type { JSX, SyntheticEvent } from 'react'
import type { DemoPersonaSummary } from './contracts'
import type { Lang } from '../customer-chat/contracts'
import type { SignInAudience } from './api'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/ErrorState'
import { Notice } from '../../components/ui/Notice'
import { fetchAgentPersonas, fetchCustomerPersonas, signIn } from './api'
import { useT } from '../../i18n/useT'
import { failureReason } from '../../i18n/failureReason'
import { LANGUAGES, LANGUAGE_NAMES } from '../../i18n/lang'
import { classifyFailure } from '../../lib/failure'
import type { FailureKind } from '../../lib/failure'
import styles from './SignInScreen.module.css'

type DirectoryStatus = 'loading' | 'ready' | 'unavailable' | 'error'

/** Before any persona is selected (loading, the directory error), nothing has told this screen
 * which language to speak in yet. Spanish is the product's first-listed, required language, so
 * it is this screen's starting point rather than a guess. */
const DEFAULT_LANG: Lang = 'es'

/** `DemoPersonaSummary.language` is a bare, unvalidated string at the wire contract (it mirrors
 * the backend's own persona model, which is not scoped to this frontend's three display
 * languages) — falls back to `DEFAULT_LANG` rather than crash on a value `useT`'s catalog lookup
 * doesn't recognize. */
function toLang(value: string): Lang {
  return (LANGUAGES as readonly string[]).includes(value) ? (value as Lang) : DEFAULT_LANG
}

/** A persona's name followed by the language it speaks, so the choice says who the conversation will be with and in what language. */
function personaLabel(persona: DemoPersonaSummary): string {
  const lang = toLang(persona.language)
  return (LANGUAGES as readonly string[]).includes(persona.language)
    ? `${persona.display_name} — ${LANGUAGE_NAMES[lang]}`
    : persona.display_name
}

/**
 * The demonstration sign-in: a card with a persona picker built from the live persona directory
 * (never a hardcoded copy) and the access code, which is handed to whoever runs the demonstration
 * out of band and never baked into this bundle.
 *
 * `audience` selects the persona list and the access code this screen asks for: `'customer'` (the
 * default) or `'agent'`.
 *
 * When the sign-in is switched off, the service either refuses the persona directory outright or
 * lists no persona for this audience; either way the screen says plainly that the demonstration
 * is not available and offers no form. A directory that fails for any other reason (no
 * connection, a slow answer, a limit reached, a server error) says which, and offers Retry.
 *
 * A refused sign-in says what was refused: a wrong access code or persona, a limit reached, or
 * a connection or server problem, each in its own words. The keyboard returns to the access code
 * field so it can be corrected at once.
 *
 * `focusForm` moves the keyboard to the persona picker as soon as the form appears, for a person
 * who has just been sent back here and would otherwise have lost their place.
 *
 * `preferredLang` selects a persona who speaks that language once the directory loads, so a
 * person sent back after a session in Portuguese or English meets a form in that language rather
 * than Spanish. `preferredSlug` picks that exact persona when they speak it, otherwise the first
 * one who does. Without either, or without a persona in that language, the first persona is
 * selected.
 *
 * `onLanguageChange` reports the language this screen is currently speaking, as the selection
 * changes, so the page around it can follow.
 *
 * `onSignedIn` receives the session token, the chosen persona's language and slug, so the caller
 * can hand them to the chat client and offer the same persona again. The token is held only for the moment it takes to pass it up;
 * nothing here ever writes it to storage.
 *
 * The customer path follows the selected persona's language; the agent path stays in Spanish, like
 * the rest of the console. Before a persona is selected (loading, the directory error) the screen
 * speaks `DEFAULT_LANG`, because no language signal exists yet.
 */
export function SignInScreen({
  audience = 'customer',
  focusForm = false,
  preferredLang,
  preferredSlug,
  onSignedIn,
  onLanguageChange,
}: {
  audience?: SignInAudience
  focusForm?: boolean
  preferredLang?: Lang
  preferredSlug?: string
  onSignedIn: (token: string, lang: Lang, slug: string) => void
  onLanguageChange?: (lang: Lang) => void
}): JSX.Element {
  const [directoryStatus, setDirectoryStatus] = useState<DirectoryStatus>('loading')
  const [personas, setPersonas] = useState<readonly DemoPersonaSummary[]>([])
  const [selectedSlug, setSelectedSlug] = useState('')
  const [accessCode, setAccessCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [signInError, setSignInError] = useState<string | null>(null)
  const [directoryFailure, setDirectoryFailure] = useState<FailureKind | null>(null)
  const [directoryAttempt, setDirectoryAttempt] = useState(0)
  const headingId = useId()
  const personaFieldId = useId()
  const accessCodeFieldId = useId()
  const hintId = useId()
  const errorId = useId()
  const personaRef = useRef<HTMLSelectElement>(null)
  const accessCodeRef = useRef<HTMLInputElement>(null)
  const selectedPersona = personas.find((candidate) => candidate.slug === selectedSlug)
  // The console stays fixed-Spanish regardless of which agent persona is selected (D91); only
  // the customer path follows the selected persona's own language.
  const activeLang: Lang =
    audience === 'agent' || selectedPersona === undefined
      ? DEFAULT_LANG
      : toLang(selectedPersona.language)
  const t = useT(activeLang)

  useEffect(() => {
    onLanguageChange?.(activeLang)
  }, [activeLang, onLanguageChange])

  useEffect(() => {
    let cancelled = false
    const fetchPersonas = audience === 'agent' ? fetchAgentPersonas : fetchCustomerPersonas
    fetchPersonas().then(
      (fetched) => {
        if (cancelled) return
        setPersonas(fetched)
        const inPreferredLang = fetched.filter(
          (persona) => toLang(persona.language) === preferredLang,
        )
        const preferred =
          inPreferredLang.find((persona) => persona.slug === preferredSlug) ?? inPreferredLang[0]
        setSelectedSlug((preferred ?? fetched[0])?.slug ?? '')
        setDirectoryStatus(fetched.length === 0 ? 'unavailable' : 'ready')
      },
      (error: unknown) => {
        if (cancelled) return
        const failure = classifyFailure(error)
        setDirectoryFailure(failure)
        setDirectoryStatus(failure === 'unauthorized' ? 'unavailable' : 'error')
      },
    )
    return () => {
      cancelled = true
    }
  }, [audience, directoryAttempt, preferredLang, preferredSlug])

  useEffect(() => {
    if (focusForm && directoryStatus === 'ready') personaRef.current?.focus()
  }, [focusForm, directoryStatus])

  useEffect(() => {
    if (signInError !== null) accessCodeRef.current?.focus()
  }, [signInError])

  function reloadDirectory(): void {
    setDirectoryStatus('loading')
    setDirectoryFailure(null)
    setDirectoryAttempt((attempt) => attempt + 1)
  }

  function handleSubmit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (selectedPersona === undefined || accessCode.trim() === '' || submitting) {
      return
    }
    setSubmitting(true)
    setSignInError(null)
    signIn(selectedPersona.slug, accessCode, audience).then(
      (token) => {
        onSignedIn(token, toLang(selectedPersona.language), selectedPersona.slug)
      },
      (error: unknown) => {
        const failure = classifyFailure(error)
        setSubmitting(false)
        setSignInError(
          failure === 'unauthorized'
            ? t('signin.refused')
            : (failureReason(failure, t) ?? t('common.error.generic')),
        )
      },
    )
  }

  if (directoryStatus === 'loading') {
    return (
      <p className={styles.status} role="status">
        {t('signin.loading')}
      </p>
    )
  }

  if (directoryStatus === 'unavailable') {
    return (
      <div className={styles.slot}>
        <Notice tone="info" role="status">
          {t('signin.unavailable')}
        </Notice>
      </div>
    )
  }

  if (directoryStatus === 'error') {
    return (
      <div className={styles.slot}>
        <ErrorState title={t('signin.unreachable')} reason={failureReason(directoryFailure, t)}>
          <Button onClick={reloadDirectory}>{t('common.retry')}</Button>
        </ErrorState>
      </div>
    )
  }

  const refused = signInError !== null
  const describedBy = [accessCode.trim() === '' ? hintId : null, refused ? errorId : null]
    .filter((id) => id !== null)
    .join(' ')

  return (
    <section aria-labelledby={headingId} className={styles.card}>
      <h2 id={headingId} className={styles.heading}>
        {t('signin.regionLabel')}
      </h2>
      <p className={styles.intro}>{t('signin.intro')}</p>
      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor={personaFieldId}>
            {t('signin.personaLabel')}
          </label>
          <select
            id={personaFieldId}
            ref={personaRef}
            className={styles.select}
            value={selectedSlug}
            disabled={submitting}
            onChange={(event) => {
              setSelectedSlug(event.target.value)
            }}
          >
            {personas.map((persona) => (
              <option key={persona.slug} value={persona.slug}>
                {personaLabel(persona)}
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
            ref={accessCodeRef}
            name="access-code"
            type="password"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            className={styles.accessCode}
            value={accessCode}
            disabled={submitting}
            aria-invalid={refused ? true : undefined}
            aria-describedby={describedBy === '' ? undefined : describedBy}
            onChange={(event) => {
              setAccessCode(event.target.value)
            }}
          />
          {accessCode.trim() === '' && (
            <p id={hintId} className={styles.hint}>
              {t('signin.accessCodeHint')}
            </p>
          )}
        </div>

        {refused && (
          <div id={errorId}>
            <ErrorState title={signInError} />
          </div>
        )}

        <Button
          type="submit"
          variant="primary"
          large
          fullWidth
          disabled={submitting || selectedSlug === '' || accessCode.trim() === ''}
        >
          {submitting ? t('signin.submitting') : t('signin.submit')}
        </Button>
      </form>
    </section>
  )
}
