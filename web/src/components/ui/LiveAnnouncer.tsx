/** A visually hidden live region for announcing changes to screen-reader users. */
import type { JSX } from 'react'
import styles from './LiveAnnouncer.module.css'

/**
 * A region that is heard but never seen: when `message` changes, a screen reader reads the new
 * text aloud without moving focus. The region is always in the page, even while empty, because
 * assistive technology only announces changes to a live region it already knows about.
 *
 * `messageKey` identifies the message: when it changes the text is replaced even if the words are
 * the same, so a repeated reply is announced again instead of being silently skipped.
 */
export function LiveAnnouncer({
  message,
  messageKey,
}: {
  message: string
  messageKey?: string
}): JSX.Element {
  return (
    <div role="status" aria-live="polite" aria-atomic="true" className={styles.announcer}>
      {message === '' ? null : <div key={messageKey}>{message}</div>}
    </div>
  )
}
