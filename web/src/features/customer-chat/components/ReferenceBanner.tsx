/**
 * Banner with the reference-date line and the optional demonstration notice.
 */
import type { JSX } from 'react'
import styles from './ReferenceBanner.module.css'

/**
 * The persistent reference-date line and the demonstration notice.
 *
 * The service renders both texts (`contracts/service_v1/api.py`: the client shows them and decides
 * nothing); this component never computes or guesses either one. It is a `note` landmark so the
 * date context stays discoverable without interrupting the conversation.
 */
export function ReferenceBanner({
  referenceDateLine,
  demoNotice,
}: {
  referenceDateLine: string
  demoNotice: string | null
}): JSX.Element {
  return (
    <div role="note" className={styles.banner}>
      <p>{referenceDateLine}</p>
      {demoNotice !== null && <p>{demoNotice}</p>}
    </div>
  )
}
