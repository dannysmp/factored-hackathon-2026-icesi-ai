/** Component test: `TurnForm` submits trimmed text and refuses a blank or whitespace-only one. */
import { fireEvent, render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { TurnForm } from './TurnForm'

describe('TurnForm', () => {
  it('submits the trimmed text and clears the field', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<TurnForm onSubmit={onSubmit} busy={false} lang="en" />)

    await user.type(screen.getByLabelText('Your message'), '  hello  ')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('hello')
    expect(screen.getByLabelText('Your message')).toHaveValue('')
  })

  it('refuses a whitespace-only submission, in case the form is ever submitted directly', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const { container } = render(<TurnForm onSubmit={onSubmit} busy={false} lang="en" />)

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

  it('stays focusable and read-only while a reply is awaited, instead of being disabled', () => {
    render(<TurnForm onSubmit={vi.fn()} busy lang="en" />)
    const input = screen.getByLabelText('Your message')

    expect(input).not.toBeDisabled()
    expect(input).toHaveAttribute('readonly')
    expect(input).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('does not submit, or clear the field, while a reply is awaited', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const { container, rerender } = render(<TurnForm onSubmit={onSubmit} busy={false} lang="en" />)
    await user.type(screen.getByLabelText('Your message'), 'draft')
    rerender(<TurnForm onSubmit={onSubmit} busy lang="en" />)

    const form = container.querySelector('form')
    if (form === null) throw new Error('expected the form')
    fireEvent.submit(form)

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Your message')).toHaveValue('draft')
  })

  it('returns focus to the field after sending', async () => {
    const user = userEvent.setup()
    const inputRef = createRef<HTMLInputElement>()
    render(<TurnForm onSubmit={vi.fn()} busy={false} lang="en" inputRef={inputRef} />)

    await user.type(screen.getByLabelText('Your message'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByLabelText('Your message')).toHaveFocus()
  })

  it('limits the message to what the endpoint accepts', () => {
    render(<TurnForm onSubmit={vi.fn()} busy={false} lang="en" />)
    expect(screen.getByLabelText('Your message')).toHaveAttribute('maxlength', '2000')
  })

  it('shows how many characters are left only as the limit gets close, and ties it to the field', () => {
    render(<TurnForm onSubmit={vi.fn()} busy={false} lang="en" />)
    const input = screen.getByLabelText('Your message')
    expect(screen.queryByText(/Characters left/)).not.toBeInTheDocument()
    expect(input).not.toHaveAttribute('aria-describedby')

    fireEvent.change(input, { target: { value: 'x'.repeat(1850) } })

    const hint = screen.getByText('Characters left: 150', { selector: 'span:not([role])' })
    expect(input).toHaveAttribute('aria-describedby', hint.id)

    fireEvent.change(input, { target: { value: 'x'.repeat(1800) } })
    expect(screen.getAllByText('Characters left: 200')).toHaveLength(2)
    fireEvent.change(input, { target: { value: 'x'.repeat(1799) } })
    expect(screen.queryByText(/Characters left/)).not.toBeInTheDocument()
  })

  it('tells a screen reader at 200, 100 and 0 characters left, and not in between', () => {
    render(<TurnForm onSubmit={vi.fn()} busy={false} lang="en" />)
    const input = screen.getByLabelText('Your message')
    const announcer = screen.getByRole('status')
    expect(announcer).toBeEmptyDOMElement()

    fireEvent.change(input, { target: { value: 'x'.repeat(1799) } })
    expect(announcer).toBeEmptyDOMElement()
    fireEvent.change(input, { target: { value: 'x'.repeat(1800) } })
    expect(announcer).toHaveTextContent('Characters left: 200')
    fireEvent.change(input, { target: { value: 'x'.repeat(1850) } })
    expect(announcer).toHaveTextContent('Characters left: 200')
    fireEvent.change(input, { target: { value: 'x'.repeat(1900) } })
    expect(announcer).toHaveTextContent('Characters left: 100')
    fireEvent.change(input, { target: { value: 'x'.repeat(2000) } })
    expect(announcer).toHaveTextContent('Characters left: 0')
    fireEvent.change(input, { target: { value: 'x'.repeat(10) } })
    expect(announcer).toBeEmptyDOMElement()
  })

  it('speaks the announcement in the conversation’s language', () => {
    render(<TurnForm onSubmit={vi.fn()} busy={false} lang="es" />)

    fireEvent.change(screen.getByLabelText('Su mensaje'), { target: { value: 'x'.repeat(2000) } })

    expect(screen.getByRole('status')).toHaveTextContent('Caracteres restantes: 0')
  })

  it('asks a phone keyboard for a send key and does not offer the browser’s own suggestions', () => {
    render(<TurnForm onSubmit={vi.fn()} busy={false} lang="en" />)
    const input = screen.getByLabelText('Your message')

    expect(input).toHaveAttribute('enterkeyhint', 'send')
    expect(input).toHaveAttribute('autocomplete', 'off')
  })

  it('has no automatically detectable accessibility violations, with the hint showing', async () => {
    const { container } = render(<TurnForm onSubmit={vi.fn()} busy={false} lang="pt" />)
    fireEvent.change(screen.getByLabelText('Sua mensagem'), { target: { value: 'x'.repeat(1900) } })
    expect(screen.getAllByText('Caracteres restantes: 100')).toHaveLength(2)
    expect(await axe(container)).toHaveNoViolations()
  })
})
