import { useId, useState } from 'react'
import type { JSX, SyntheticEvent } from 'react'

/**
 * The free-text input, always available alongside any choices or the confirmation button: a
 * customer may type instead of clicking either one.
 */
export function TurnForm({
  onSubmit,
  disabled,
}: {
  onSubmit: (text: string) => void
  disabled: boolean
}): JSX.Element {
  const [text, setText] = useState('')
  const inputId = useId()

  function handleSubmit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = text.trim()
    if (trimmed === '') {
      return
    }
    onSubmit(trimmed)
    setText('')
  }

  return (
    <form onSubmit={handleSubmit}>
      <label htmlFor={inputId}>Your message</label>
      <input
        id={inputId}
        type="text"
        value={text}
        disabled={disabled}
        onChange={(event) => {
          setText(event.target.value)
        }}
      />
      <button type="submit" disabled={disabled || text.trim() === ''}>
        Send
      </button>
    </form>
  )
}
