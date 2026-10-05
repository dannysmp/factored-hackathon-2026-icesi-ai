/** The confirmation button shown while the assistant is waiting for the customer to confirm. */
import type { JSX } from 'react'
import { Button } from '../../../components/ui/Button'
import { CONFIRMATION_TEXT, DECLINE_TEXT } from '../contracts'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import styles from './ConfirmationPrompt.module.css'

/**
 * The yes and no quick replies next to the text prompt.
 *
 * A click supplies the same fixed text an explicit typed "yes" or "no" would; neither carries a
 * summary of its own: the reply above them already showed what is being confirmed. The conversation
 * shows the button's own label, in the customer's language, as what they said. Yes is the one
 * primary action; No is outlined beside it, so declining is as easy to reach as agreeing without
 * competing with it. The caller renders the pair only while a confirmation is awaited and no turn
 * is in flight, so a changed summary is never answered by a stale button.
 */
export function ConfirmationPrompt({
  onConfirm,
  lang,
}: {
  onConfirm: (sent: string, shown: string) => void
  lang: Lang
}): JSX.Element {
  const t = useT(lang)
  return (
    <div className={styles.confirm} role="group" aria-label={t('chat.quickReplies')}>
      <Button
        variant="primary"
        large
        onClick={() => {
          onConfirm(CONFIRMATION_TEXT, t('chat.confirm'))
        }}
      >
        {t('chat.confirm')}
      </Button>
      <Button
        variant="secondary"
        large
        onClick={() => {
          onConfirm(DECLINE_TEXT, t('chat.decline'))
        }}
      >
        {t('chat.decline')}
      </Button>
    </div>
  )
}
