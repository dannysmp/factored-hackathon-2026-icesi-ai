import type { JSX, Ref } from 'react'
import { useId } from 'react'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import { classNames } from '../../../components/ui/classNames'
import { resultOf } from '../result'
import type { ResultVariant } from '../result'
import styles from './ResultCard.module.css'

/** One glyph per outcome, so the outcome never relies on color alone. */
function OutcomeIcon({ variant }: { variant: ResultVariant }): JSX.Element {
  return (
    <svg
      className={styles.icon}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      {variant === 'filed' && <path d="M7.5 12.5l3 3 6-6.5" />}
      {variant === 'escalated' && (
        <>
          <circle cx="12" cy="9.5" r="2.5" />
          <path d="M7 17.5c.8-2.2 2.6-3.2 5-3.2s4.2 1 5 3.2" />
        </>
      )}
      {variant === 'closed' && <path d="M8 12h8" />}
    </svg>
  )
}

/**
 * How a conversation that has ended is presented, so the outcome is the first thing the customer
 * sees rather than a line inside a message.
 *
 * Three outcomes read differently and look different: a dispute that was filed (success tone, its
 * case number), a request handed to a person (information tone, the reference to quote), and a
 * conversation that simply ended with no case (neutral); each has its own glyph and title. The
 * number sits in its own block, large and in tabular figures, so it can be read aloud or copied
 * without a mistake. `titleRef` lets the screen move focus to the outcome once the conversation
 * ends. A hand-off that follows a filing
 * leads with its own reference and lists the filed case beneath it.
 */
export function ResultCard({
  lang,
  caseNumber,
  handoffTicket,
  titleRef,
}: {
  lang: Lang
  caseNumber: string | null
  handoffTicket: string | null
  titleRef?: Ref<HTMLHeadingElement>
}): JSX.Element {
  const t = useT(lang)
  const titleId = useId()
  const { variant, reference, filedEarlier } = resultOf(caseNumber, handoffTicket)
  const title = t(`chat.result.${variant}Title`)

  return (
    <section className={classNames(styles.card, styles[variant])} aria-labelledby={titleId}>
      <header className={styles.header}>
        <OutcomeIcon variant={variant} />
        <h2 id={titleId} ref={titleRef} tabIndex={-1} className={styles.title}>
          {title}
        </h2>
      </header>
      {variant === 'escalated' && <p className={styles.next}>{t('chat.result.escalatedNext')}</p>}
      {reference !== null && (
        <>
          <div className={styles.ticket}>
            <p className={styles.label}>{t('chat.result.caseNumberLabel')}</p>
            <p className={styles.reference}>{reference}</p>
          </div>
          <p className={styles.keep}>{t('chat.result.keepNumber')}</p>
        </>
      )}
      {filedEarlier !== null && (
        <div className={styles.earlier}>
          <p className={styles.label}>{t('chat.result.filedEarlierLabel')}</p>
          <p className={styles.filedEarlier}>{filedEarlier}</p>
        </div>
      )}
    </section>
  )
}
