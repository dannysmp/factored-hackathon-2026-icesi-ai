/** Component test: the whole conversation can be driven from the keyboard alone. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'

function renderChat(): ReturnType<typeof render> {
  return render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN)} lang="en" />)
}

describe('ChatFeature keyboard traversal', () => {
  it('reaches the message field first, then Send once there is text to send', async () => {
    const user = userEvent.setup()
    renderChat()
    await screen.findByText('Hi! Which transaction would you like to dispute?')

    await user.tab()
    expect(screen.getByLabelText('Your message')).toHaveFocus()

    // Send is disabled while the field is empty, so Tab skips it rather than landing on a dead
    // control.
    await user.tab()
    expect(screen.getByLabelText('Your message')).not.toHaveFocus()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).not.toHaveFocus()

    await user.tab({ shift: true })
    await user.keyboard('the Tienda Sol one')
    await user.tab()
    expect(screen.getByRole('button', { name: 'Send' })).toHaveFocus()
  })

  it('walks the whole conversation with Tab, Enter and Space alone', async () => {
    const user = userEvent.setup()
    renderChat()
    await screen.findByText('Hi! Which transaction would you like to dispute?')

    await user.tab()
    await user.keyboard('the Tienda Sol one')
    await user.tab()
    await user.keyboard('{Enter}')

    // The numbered choice comes before the text field in the tab order.
    const choice = await screen.findByRole('button', {
      name: '1. MXN 250.00 at Tienda Sol on 12 June 2026',
    })
    await user.tab()
    expect(choice).toHaveFocus()
    await user.keyboard(' ')

    await screen.findByText(/what is the reason for the dispute/i)
    await user.tab()
    expect(screen.getByLabelText('Your message')).toHaveFocus()
    await user.keyboard('unrecognized charge')
    await user.tab()
    await user.keyboard('{Enter}')

    // The confirmation button also precedes the field, and Enter on it files the case.
    const confirm = await screen.findByRole('button', { name: 'Confirm' })
    // The next Tab stop after the reply is the confirmation button, ahead of the field.
    await user.tab()
    expect(confirm).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText(/case DEMO-1234/)).toBeInTheDocument()
  })
})
