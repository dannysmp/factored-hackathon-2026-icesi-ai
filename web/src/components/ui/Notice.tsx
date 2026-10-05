import type { JSX, ReactNode } from 'react'
import { classNames } from './classNames'
import styles from './Notice.module.css'

export type NoticeTone = 'info' | 'success' | 'warning' | 'error'

/** One glyph per tone, so a notice never relies on color alone to say what kind it is. */
const GLYPHS: Record<NoticeTone, string> = {
  info: 'M12 8h.01M11 12h1v5h1',
  success: 'M7 12.5l3 3 7-7',
  warning: 'M12 8v5m0 3h.01',
  error: 'M9 9l6 6m0-6l-6 6',
}

/**
 * A message the page needs the person to see: an icon, the text, and a tinted surface in the
 * tone's color. `role` is the caller's choice because it depends on the moment: `alert` for a
 * failure the person must hear at once, `status` for a result they should hear without being
 * interrupted, none for a note that is simply part of the page.
 */
export function Notice({
  tone,
  role,
  children,
}: {
  tone: NoticeTone
  role?: 'alert' | 'status'
  children: ReactNode
}): JSX.Element {
  return (
    <div className={classNames(styles.notice, styles[tone])} role={role}>
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
        {tone === 'warning' ? <path d="M12 3.5l9.5 16.5h-19z" /> : <circle cx="12" cy="12" r="9" />}
        <path d={GLYPHS[tone]} />
      </svg>
      <div className={styles.body}>{children}</div>
    </div>
  )
}
