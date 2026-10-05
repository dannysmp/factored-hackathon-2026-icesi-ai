/** Component test: each offered option is a button that sends its number and can be disabled. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ChoiceButtons } from './ChoiceButtons'

const CHOICES = [
  { number: 1, label: 'Cafetería Central' },
  { number: 2, label: 'Supermercado Norte' },
]

describe('ChoiceButtons', () => {
  it('renders nothing when there is nothing to choose', () => {
    const { container } = render(<ChoiceButtons choices={[]} onChoose={vi.fn()} disabled={false} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('sends the number of the option that was pressed, with its label as the words shown', async () => {
    const onChoose = vi.fn()
    const user = userEvent.setup()
    render(<ChoiceButtons choices={CHOICES} onChoose={onChoose} disabled={false} />)

    await user.click(screen.getByRole('button', { name: '2. Supermercado Norte' }))

    expect(onChoose).toHaveBeenCalledExactlyOnceWith('2', 'Supermercado Norte')
  })

  it('disables every option while a turn is in flight', () => {
    render(<ChoiceButtons choices={CHOICES} onChoose={vi.fn()} disabled />)

    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  })
})
