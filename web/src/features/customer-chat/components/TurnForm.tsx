import { useId, useState } from 'react'
import type { JSX, RefObject, SyntheticEvent } from 'react'
import { Button } from '../../../components/ui/Button'
import { LiveAnnouncer } from '../../../components/ui/LiveAnnouncer'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import { MAX_TURN_TEXT_LENGTH } from '../contracts'
import styles from './TurnForm.module.css'

/** How close to the limit the remaining-characters hint appears. */
const HINT_THRESHOLD = 200

/** The remaining counts a screen reader is told about while typing, nearest the limit last. */
const ANNOUNCE_STEPS = [200, 100, 0] as const

/** The step the remaining count has reached, or `null` while it is still comfortably far away. */
function announceStep(remaining: number): number | null {
  return ANNOUNCE_STEPS.findLast((step) => remaining <= step) ?? null
}

/**
 * The free-text input, always available alongside any choices or the confirmation button: a
 * customer may type instead of clicking either one.
 *
 * While a reply is awaited the field is read-only rather than disabled, so a person typing or
 * using a screen reader keeps their place instead of losing focus to the page. After a message
 * is sent, focus returns to the field. A hint with the remaining characters appears as the
 * limit gets close, so a long message is never refused without warning; a screen reader is told
 * at 200, 100 and 0 characters left, so a paste cut at the limit is never silent.
 */
export function TurnForm({
  onSubmit,
  busy,
  lang,
  inputRef,
}: {
  onSubmit: (text: string) => void
  busy: boolean
  lang: Lang
  inputRef?: RefObject<HTMLInputElement | null>
}): JSX.Element {
  const [text, setText] = useState('')
  const inputId = useId()
  const hintId = useId()
  const t = useT(lang)
  const remaining = MAX_TURN_TEXT_LENGTH - text.length
  const showHint = remaining <= HINT_THRESHOLD
  const step = announceStep(remaining)
  const announcement =
    step === null ? '' : t('chat.charactersLeft').replace('{count}', String(step))

  function handleSubmit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = text.trim()
    if (trimmed === '' || busy) {
      return
    }
    onSubmit(trimmed)
    setText('')
    inputRef?.current?.focus()
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <LiveAnnouncer message={announcement} />
      <div className={styles.field}>
        <label className={styles.label} htmlFor={inputId}>
          {t('chat.messageLabel')}
        </label>
        <input
          id={inputId}
          ref={inputRef}
          type="text"
          className={styles.input}
          value={text}
          maxLength={MAX_TURN_TEXT_LENGTH}
          readOnly={busy}
          aria-busy={busy}
          aria-describedby={showHint ? hintId : undefined}
          autoComplete="off"
          enterKeyHint="send"
          onChange={(event) => {
            setText(event.target.value)
          }}
        />
        {showHint && (
          <span id={hintId} className={styles.hint}>
            {t('chat.charactersLeft').replace('{count}', String(remaining))}
          </span>
        )}
      </div>
      <Button type="submit" variant="primary" disabled={busy || text.trim() === ''}>
        {t('chat.send')}
      </Button>
    </form>
  )
}
