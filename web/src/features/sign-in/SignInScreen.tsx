/**
 * The demonstration sign-in screen for the customer chat and the agent console.
 */
import { useEffect, useId, useRef, useState } from 'react'
import type { JSX, SyntheticEvent } from 'react'
import type { DemoPersonaSummary } from './contracts'
import type { Lang } from '../customer-chat/contracts'
import type { SignInAudience } from './api'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/ErrorState'
import { Notice } from '../../components/ui/Notice'
import { classNames } from '../../components/ui/classNames'
import { fetchAgentPersonas, fetchCustomerPersonas, signIn } from './api'
import { useT } from '../../i18n/useT'
import { failureReason } from '../../i18n/failureReason'
import { LANGUAGES, LANGUAGE_NAMES } from '../../i18n/lang'
import { PERSONA_CASE_KEYS, personaInitials } from './personaCases'
import { classifyFailure } from '../../lib/failure'
import type { FailureKind } from '../../lib/failure'
import styles from './SignInScreen.module.css'

/** Where the persona directory is: `unavailable` means sign-in is switched off or offers no persona. */
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

/** Whether a persona's language is one of the three the screen is shown in. */
function isShownLanguage(value: string): value is Lang {
  return (LANGUAGES as readonly string[]).includes(value)
}

/**
 * The demonstration sign-in: a centered card with the product's name and a one-line description,
 * a language switcher, a card for each persona built from the live persona directory (never a
 * hardcoded copy) and the access code, which is handed to whoever runs the demonstration out of
 * band and never baked into this bundle.
 *
 * Each persona card shows initials, the name, the language the persona speaks and, in plain words,
 * the case the persona represents. The language switcher selects the first persona who speaks the
 * chosen language, unless the selected persona already does; a language no persona speaks is
 * offered but disabled. The access code can be shown or hidden.
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
 * `focusForm` moves the keyboard to the selected persona card as soon as the form appears, for a person
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
 * can hand them to the chat client and offer the same persona again. The token is passed straight
 * up; nothing here writes it to storage.
 *
 * The customer path follows the selected persona's language; the agent path stays in Spanish, like
 * the rest of the console, and has no language switcher. Before a persona is selected (loading, the directory error) the screen
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
  const languageLabelId = useId()
  const personaGroupId = useId()
  const accessCodeFieldId = useId()
  const hintId = useId()
  const errorId = useId()
  const [accessCodeVisible, setAccessCodeVisible] = useState(false)
  const personaGroupRef = useRef<HTMLFieldSetElement>(null)
  const accessCodeRef = useRef<HTMLInputElement>(null)
  const selectedPersona = personas.find((candidate) => candidate.slug === selectedSlug)
  // The console stays fixed-Spanish regardless of which agent persona is selected; only
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
    if (focusForm && directoryStatus === 'ready')
      personaGroupRef.current?.querySelector<HTMLInputElement>('input:checked')?.focus()
  }, [focusForm, directoryStatus])

  useEffect(() => {
    if (signInError !== null) accessCodeRef.current?.focus()
  }, [signInError])

  function reloadDirectory(): void {
    setDirectoryStatus('loading')
    setDirectoryFailure(null)
    setDirectoryAttempt((attempt) => attempt + 1)
  }

  function chooseLanguage(lang: Lang): void {
    if (selectedPersona !== undefined && toLang(selectedPersona.language) === lang) return
    const first = personas.find((persona) => toLang(persona.language) === lang)
    if (first !== undefined) setSelectedSlug(first.slug)
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
      <p className={styles.eyebrow}>{t('signin.regionLabel')}</p>
      <h2 id={headingId} className={styles.heading}>
        {t('signin.productName')}
      </h2>
      <p className={styles.tagline}>
        {t(audience === 'agent' ? 'signin.agentTagline' : 'signin.productTagline')}
      </p>
      {audience === 'customer' && (
        <div className={styles.languageSwitcher}>
          <span id={languageLabelId} className={styles.label}>
            {t('signin.languageSwitcherLabel')}
          </span>
          <div role="group" aria-labelledby={languageLabelId} className={styles.languageButtons}>
            {LANGUAGES.map((lang) => (
              <button
                key={lang}
                type="button"
                lang={lang}
                className={styles.languageButton}
                aria-pressed={activeLang === lang}
                disabled={
                  submitting || !personas.some((persona) => toLang(persona.language) === lang)
                }
                onClick={() => {
                  chooseLanguage(lang)
                }}
              >
                {LANGUAGE_NAMES[lang]}
              </button>
            ))}
          </div>
        </div>
      )}
      <p className={styles.intro}>{t('signin.intro')}</p>
      <form className={styles.form} onSubmit={handleSubmit}>
        <fieldset
          ref={personaGroupRef}
          className={styles.personas}
          aria-labelledby={personaGroupId}
          disabled={submitting}
        >
          <legend id={personaGroupId} className={styles.label}>
            {t('signin.personaGroupLabel')}
          </legend>
          {personas.map((persona) => {
            const caseKey = PERSONA_CASE_KEYS[persona.slug]
            const selected = persona.slug === selectedSlug
            return (
              <label
                key={persona.slug}
                className={classNames(styles.persona, selected && styles.personaSelected)}
              >
                <input
                  type="radio"
                  name="persona"
                  value={persona.slug}
                  checked={selected}
                  className={styles.personaRadio}
                  onChange={() => {
                    setSelectedSlug(persona.slug)
                  }}
                />
                <span className={styles.avatar} aria-hidden="true">
                  {personaInitials(persona.display_name)}
                </span>
                <span className={styles.personaText}>
                  <span className={styles.personaHeader}>
                    <span className={styles.personaName}>{persona.display_name}</span>
                    {isShownLanguage(persona.language) && (
                      <span className={styles.personaLanguage}>
                        {t(`signin.personaLanguage.${persona.language}`)}
                      </span>
                    )}
                  </span>
                  {caseKey !== undefined && (
                    <span className={styles.personaCase}>{t(caseKey)}</span>
                  )}
                </span>
              </label>
            )
          })}
        </fieldset>

        <div className={styles.field}>
          <label className={styles.label} htmlFor={accessCodeFieldId}>
            {t('signin.accessCodeLabel')}
          </label>
          <div className={styles.accessCodeRow}>
            <input
              id={accessCodeFieldId}
              ref={accessCodeRef}
              name="access-code"
              type={accessCodeVisible ? 'text' : 'password'}
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
            <button
              type="button"
              className={styles.accessCodeToggle}
              aria-label={`${t(accessCodeVisible ? 'signin.accessCodeHide' : 'signin.accessCodeShow')} ${t('signin.accessCodeLabel').toLocaleLowerCase(activeLang)}`}
              aria-controls={accessCodeFieldId}
              disabled={submitting}
              onClick={() => {
                setAccessCodeVisible((visible) => !visible)
              }}
            >
              {t(accessCodeVisible ? 'signin.accessCodeHide' : 'signin.accessCodeShow')}
            </button>
          </div>
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
