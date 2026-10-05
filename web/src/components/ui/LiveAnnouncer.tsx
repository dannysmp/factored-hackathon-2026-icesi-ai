import type { JSX } from 'react'
import styles from './LiveAnnouncer.module.css'

/**
 * A region that is heard but never seen: when `message` changes, a screen reader reads the new
 * text aloud without moving focus. The region is always in the page, even while empty, because
 * assistive technology only announces changes to a live region it already knows about.
 */
export function LiveAnnouncer({ message }: { message: string }): JSX.Element {
  return (
    <div role="status" aria-live="polite" aria-atomic="true" className={styles.announcer}>
      {message}
    </div>
  )
}
