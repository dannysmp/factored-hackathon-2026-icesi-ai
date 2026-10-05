import type { JSX } from 'react'
import { useId } from 'react'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import { classNames } from '../../../components/ui/classNames'
import { resultOf } from '../result'
import styles from './ResultCard.module.css'

/**
 * How a conversation that has ended is presented, so the outcome is the first thing the customer
 * sees rather than a line inside a message.
 *
 * Three outcomes read differently and look different: a dispute that was filed (success tone, its
 * case number), a request handed to a person (information tone, the reference to quote), and a
 * conversation that simply ended with no case (neutral). The number is set large in tabular
 * figures so it can be read aloud or copied without a mistake. Filing wins over hand-off when the
 * service reports both.
 */
export function ResultCard({
  lang,
  caseNumber,
  handoffTicket,
}: {
  lang: Lang
  caseNumber: string | null
  handoffTicket: string | null
}): JSX.Element {
  const t = useT(lang)
  const titleId = useId()
  const { variant, reference } = resultOf(caseNumber, handoffTicket)
  const title = t(`chat.result.${variant}Title`)

  return (
    <section className={classNames(styles.card, styles[variant])} aria-labelledby={titleId}>
      <h2 id={titleId} className={styles.title}>
        {title}
      </h2>
      {reference !== null && (
        <>
          <p className={styles.label}>{t('chat.result.caseNumberLabel')}</p>
          <p className={styles.reference}>{reference}</p>
          <p className={styles.next}>{t('chat.result.keepNumber')}</p>
        </>
      )}
    </section>
  )
}
