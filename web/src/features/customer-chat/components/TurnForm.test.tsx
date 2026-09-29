/** Component test: `TurnForm` submits trimmed text and refuses a blank or whitespace-only one. */
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TurnForm } from './TurnForm'

describe('TurnForm', () => {
  it('submits the trimmed text and clears the field', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<TurnForm onSubmit={onSubmit} disabled={false} lang="en" />)

    await user.type(screen.getByLabelText('Your message'), '  hello  ')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('hello')
    expect(screen.getByLabelText('Your message')).toHaveValue('')
  })

  it('refuses a whitespace-only submission, in case the form is ever submitted directly', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const { container } = render(<TurnForm onSubmit={onSubmit} disabled={false} lang="en" />)

    await user.type(screen.getByLabelText('Your message'), '   ')
    // The submit button is already disabled for whitespace-only text, which itself suppresses a
    // browser's implicit submit-on-Enter; this dispatches the form's `submit` event directly, so
    // the handler's own guard is exercised regardless of what disables the button.
    const form = container.querySelector('form')
    expect(form).not.toBeNull()
    if (form !== null) {
      fireEvent.submit(form)
    }

    expect(onSubmit).not.toHaveBeenCalled()
  })
})
