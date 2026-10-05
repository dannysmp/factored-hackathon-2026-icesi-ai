/** The confirmation button shown while the assistant is waiting for the customer to confirm. */
import { useId } from 'react'
import type { JSX } from 'react'
import { Button } from '../../../components/ui/Button'
import { CONFIRMATION_TEXT, DECLINE_TEXT } from '../contracts'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import styles from './ConfirmationPrompt.module.css'

/**
 * The yes and no quick replies next to the text prompt, framed as a review: a title saying what
 * the step is and the reassurance that nothing is filed until the customer says yes.
 *
 * A click supplies the same fixed text an explicit typed "yes" or "no" would; neither carries a
 * summary of its own: the reply above already showed what is being confirmed. The conversation
 * shows the button's own label, in the customer's language, as what they said. Yes is the one
 * primary action; No is outlined beside it, so declining is as easy to reach as agreeing without
 * outweighing it. The frame belongs to the buttons, not to a transcript message, so it can never
 * sit on a reply that is not the summary. The caller renders it only while a confirmation is
 * awaited and no turn is in flight, so a changed summary is never answered by a stale button.
 */
export function ConfirmationPrompt({
  onConfirm,
  lang,
}: {
  onConfirm: (sent: string, shown: string) => void
  lang: Lang
}): JSX.Element {
  const t = useT(lang)
  const hintId = useId()
  return (
    <div className={styles.review}>
      <p className={styles.title}>{t('chat.review.title')}</p>
      <p id={hintId} className={styles.hint}>
        {t('chat.review.hint')}
      </p>
      <div
        className={styles.confirm}
        role="group"
        aria-label={t('chat.quickReplies')}
        aria-describedby={hintId}
      >
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
    </div>
  )
}
