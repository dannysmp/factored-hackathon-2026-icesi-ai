import type { JSX } from 'react'
import { CONFIRMATION_TEXT } from '../contracts'

/**
 * The confirmation button next to the text prompt (AC-E10-13).
 *
 * A click supplies the same fixed text an explicit typed "yes" would (AC-E5-20); it never
 * carries its own summary of what is being confirmed; the reply above it already showed that.
 * The live endpoint slice must disable or re-show this after any change to that summary — a
 * fixture script never changes mid-conversation, so this component cannot exercise that rule.
 */
export function ConfirmationPrompt({
  onConfirm,
  disabled,
}: {
  onConfirm: (text: string) => void
  disabled: boolean
}): JSX.Element {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        onConfirm(CONFIRMATION_TEXT)
      }}
    >
      Confirm
    </button>
  )
}
