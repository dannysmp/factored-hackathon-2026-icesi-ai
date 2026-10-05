/**
 * Buttons for the numbered options the assistant offers.
 */
import type { JSX } from 'react'
import { Button } from '../../../components/ui/Button'
import type { Choice } from '../contracts'
import styles from './ChoiceButtons.module.css'

/**
 * The numbered options the assistant offered, each a real button
 * (semantic HTML first). A click sends the option's number, exactly as a customer typing it would,
 * while the conversation shows the option's full description as what the customer said.
 * Renders nothing when there are no options; `disabled` blocks clicks while a reply is awaited.
 */
export function ChoiceButtons({
  choices,
  onChoose,
  disabled,
}: {
  choices: readonly Choice[]
  onChoose: (sent: string, shown: string) => void
  disabled: boolean
}): JSX.Element | null {
  if (choices.length === 0) {
    return null
  }
  return (
    <ul className={styles.list}>
      {choices.map((choice) => (
        <li key={choice.number}>
          <Button
            className={styles.choice}
            disabled={disabled}
            onClick={() => {
              onChoose(String(choice.number), choice.label)
            }}
          >
            {choice.number}. {choice.label}
          </Button>
        </li>
      ))}
    </ul>
  )
}
