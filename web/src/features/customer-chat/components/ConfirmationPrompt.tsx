import type { JSX } from 'react'
import { Button } from '../../../components/ui/Button'
import { CONFIRMATION_TEXT } from '../contracts'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import styles from './ConfirmationPrompt.module.css'

/**
 * The confirmation button next to the text prompt.
 *
 * A click supplies the same fixed text an explicit typed "yes" would; it never carries its own
 * summary of what is being confirmed, because the reply above it already showed that. The
 * conversation shows the button's own label, in the customer's language, as what they said.
 * The button carries no knowledge of what is being confirmed, so it is shown only while the
 * assistant is waiting for a confirmation and `disabled` while a reply is awaited.
 */
export function ConfirmationPrompt({
  onConfirm,
  disabled,
  lang,
}: {
  onConfirm: (sent: string, shown: string) => void
  disabled: boolean
  lang: Lang
}): JSX.Element {
  const t = useT(lang)
  return (
    <div className={styles.confirm}>
      <Button
        variant="primary"
        large
        fullWidth
        disabled={disabled}
        onClick={() => {
          onConfirm(CONFIRMATION_TEXT, t('chat.confirm'))
        }}
      >
        {t('chat.confirm')}
      </Button>
    </div>
  )
}
