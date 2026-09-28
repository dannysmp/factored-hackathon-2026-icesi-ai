import { useEffect, useId, useState } from 'react'
import type { JSX, SyntheticEvent } from 'react'
import type { DemoPersonaSummary } from './contracts'
import type { Lang } from '../customer-chat/contracts'
import { fetchCustomerPersonas, signIn } from './api'

type DirectoryStatus = 'loading' | 'ready' | 'error'

/**
 * The demo sign-in screen (ADR-18): a persona picker built from the real, live persona directory
 * (never a hardcoded copy — `GET /v1/auth/demo-personas`) plus the access code, distributed
 * out-of-band to whoever runs the demonstration, never baked into this bundle.
 *
 * `onSignedIn` receives the session token and the chosen persona's language, so the caller can
 * hand both to `LiveChatClient` — the token is this component's own state, held only for the
 * moment it takes to pass it up; nothing here ever writes it to storage (ADR-18: "the token held
 * in memory only").
 */
export function SignInScreen({
  onSignedIn,
}: {
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

  useEffect(() => {
    let cancelled = false
    fetchCustomerPersonas().then(
      (fetched) => {
        if (cancelled) return
        setPersonas(fetched)
        setSelectedSlug(fetched[0]?.slug ?? '')
        setDirectoryStatus('ready')
      },
      () => {
        if (cancelled) return
        setDirectoryStatus('error')
      },
    )
    return () => {
      cancelled = true
    }
  }, [])

  function handleSubmit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault()
    const persona = personas.find((candidate) => candidate.slug === selectedSlug)
    if (persona === undefined || accessCode.trim() === '' || submitting) {
      return
    }
    setSubmitting(true)
    setSignInError(null)
    signIn(persona.slug, accessCode).then(
      (token) => {
        onSignedIn(token, persona.language as Lang)
      },
      () => {
        setSubmitting(false)
        setSignInError('The access code or persona was refused. Please try again.')
      },
    )
  }

  if (directoryStatus === 'loading') {
    return (
      <p aria-live="polite" role="status">
        Loading the demonstration sign-in…
      </p>
    )
  }

  if (directoryStatus === 'error') {
    return (
      <div role="alert">
        <p>The demonstration sign-in could not be reached. Please try again.</p>
      </div>
    )
  }

  return (
    <section aria-label="Demonstration sign-in">
      <p>This is a demonstration. Sign in with one of the personas below.</p>
      <form onSubmit={handleSubmit}>
        <label htmlFor={personaFieldId}>Persona</label>
        <select
          id={personaFieldId}
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

        <label htmlFor={accessCodeFieldId}>Access code</label>
        <input
          id={accessCodeFieldId}
          type="password"
          value={accessCode}
          disabled={submitting}
          onChange={(event) => {
            setAccessCode(event.target.value)
          }}
        />

        {signInError !== null && <p role="alert">{signInError}</p>}

        <button type="submit" disabled={submitting || selectedSlug === '' || accessCode === ''}>
          Sign in
        </button>
      </form>
    </section>
  )
}
