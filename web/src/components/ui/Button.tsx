import type { ComponentProps, JSX } from 'react'
import styles from './Button.module.css'
import { classNames } from './classNames'

export type ButtonVariant = 'primary' | 'secondary' | 'quiet'

/**
 * The one button every screen uses, so an action looks and behaves the same wherever it appears.
 *
 * `primary` is the single most important action on a screen (solid accent), `secondary` an
 * alternative or a way back (outlined), `quiet` a low-emphasis action that should not compete with
 * the content around it. Every size keeps a tap target of at least 44px; a disabled button shows a
 * dedicated muted treatment rather than a faded copy of its active state.
 *
 * The type defaults to `button`, so a button inside a form never submits by accident; a submit
 * button asks for `type="submit"` explicitly.
 */
export function Button({
  variant = 'secondary',
  large = false,
  fullWidth = false,
  type = 'button',
  className,
  ...rest
}: {
  variant?: ButtonVariant
  large?: boolean
  fullWidth?: boolean
} & ComponentProps<'button'>): JSX.Element {
  const classes = classNames(
    styles.button,
    styles[variant],
    large && styles.large,
    fullWidth && styles.fullWidth,
    className,
  )

  return <button type={type} className={classes} {...rest} />
}
