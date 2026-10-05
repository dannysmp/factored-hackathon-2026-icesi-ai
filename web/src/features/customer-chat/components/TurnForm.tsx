import { useId, useState } from 'react'
import type { JSX, SyntheticEvent } from 'react'
import { Button } from '../../../components/ui/Button'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import styles from './TurnForm.module.css'

/**
 * The free-text input, always available alongside any choices or the confirmation button: a
 * customer may type instead of clicking either one.
 */
export function TurnForm({
  onSubmit,
  disabled,
  lang,
}: {
  onSubmit: (text: string) => void
  disabled: boolean
  lang: Lang
}): JSX.Element {
  const [text, setText] = useState('')
  const inputId = useId()
  const t = useT(lang)

  function handleSubmit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = text.trim()
    if (trimmed === '') {
      return
    }
    onSubmit(trimmed)
    setText('')
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <div className={styles.field}>
        <label className={styles.label} htmlFor={inputId}>
          {t('chat.messageLabel')}
        </label>
        <input
          id={inputId}
          type="text"
          className={styles.input}
          value={text}
          disabled={disabled}
          onChange={(event) => {
            setText(event.target.value)
          }}
        />
      </div>
      <Button type="submit" variant="primary" disabled={disabled || text.trim() === ''}>
        {t('chat.send')}
      </Button>
    </form>
  )
}
