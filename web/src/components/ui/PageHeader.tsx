import type { JSX, ReactNode } from 'react'
import { classNames } from './classNames'
import styles from './PageHeader.module.css'

/**
 * The bar across the top of every screen: the product mark and the page's one `h1`, with an
 * optional slot on the right for actions that belong to the whole page (signing out, for example).
 *
 * It renders a `banner` landmark, so it sits beside `main` rather than inside it. `width` matches
 * the content below: `narrow` for the single-column chat and sign-in, `wide` for the console's
 * tables, so the header's edges line up with the page on every viewport.
 */
export function PageHeader({
  title,
  width = 'narrow',
  children,
}: {
  title: string
  width?: 'narrow' | 'wide'
  children?: ReactNode
}): JSX.Element {
  return (
    <header className={styles.header}>
      <div className={classNames(styles.inner, width === 'wide' ? styles.wide : styles.narrow)}>
        <div className={styles.brand}>
          <svg className={styles.mark} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
            <rect width="32" height="32" rx="7" fill="currentColor" />
            <path
              d="M9 16.5l4.5 4.5L23 11"
              fill="none"
              stroke="var(--color-accent-contrast)"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <h1 className={styles.title}>{title}</h1>
        </div>
        {children === undefined ? null : <div className={styles.actions}>{children}</div>}
      </div>
    </header>
  )
}
