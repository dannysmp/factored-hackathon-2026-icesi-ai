import type { JSX } from 'react'
import type { Choice } from '../contracts'
import styles from './ChoiceButtons.module.css'

/**
 * The numbered options the assistant offered, each a real button (frontend standard, section 7:
 * semantic HTML first). A click sends the option's own label text, on the same terms as a
 * customer typing it.
 */
export function ChoiceButtons({
  choices,
  onChoose,
  disabled,
}: {
  choices: readonly Choice[]
  onChoose: (label: string) => void
  disabled: boolean
}): JSX.Element | null {
  if (choices.length === 0) {
    return null
  }
  return (
    <ul className={styles.list}>
      {choices.map((choice) => (
        <li key={choice.number}>
          <button
            type="button"
            className={styles.choice}
            disabled={disabled}
            onClick={() => {
              onChoose(choice.label)
            }}
          >
            {choice.number}. {choice.label}
          </button>
        </li>
      ))}
    </ul>
  )
}
