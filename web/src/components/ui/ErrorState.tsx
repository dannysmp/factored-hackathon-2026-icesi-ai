/** The shared failure message: title, optional reason and actions, announced as an alert. */
import type { JSX, ReactNode } from 'react'
import { Notice } from './Notice'
import styles from './ErrorState.module.css'

/**
 * A failure the person needs to hear about at once, written the same way wherever it happens:
 * what did not work, then why and what to do about it, then the actions that are open to them
 * (usually Retry). The reason is left out when there is nothing more to say than the title.
 *
 * It is announced as an alert, so a screen reader reads it as soon as it appears.
 */
export function ErrorState({
  title,
  reason,
  children,
}: {
  title: string
  reason?: string
  children?: ReactNode
}): JSX.Element {
  return (
    <Notice tone="error" role="alert">
      <p className={styles.title}>{title}</p>
      {reason !== undefined && <p className={styles.reason}>{reason}</p>}
      {children !== undefined && <div className={styles.actions}>{children}</div>}
    </Notice>
  )
}
