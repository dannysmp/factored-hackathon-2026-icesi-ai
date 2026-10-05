import type { JSX } from 'react'
import { Button } from '../../../components/ui/Button'
import { CONFIRMATION_TEXT } from '../contracts'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import styles from './ConfirmationPrompt.module.css'

/**
 * The confirmation button next to the text prompt (AC-E10-13).
 *
 * A click supplies the same fixed text an explicit typed "yes" would (AC-E5-20); it never
 * carries its own summary of what is being confirmed; the reply above it already showed that.
 * The conversation shows the button's own label, in the customer's language, as what they said.
 * The live endpoint slice must disable or re-show this after any change to that summary — a
 * fixture script never changes mid-conversation, so this component cannot exercise that rule.
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
