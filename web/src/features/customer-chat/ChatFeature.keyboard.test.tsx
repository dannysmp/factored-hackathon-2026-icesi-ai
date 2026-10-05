/** Component test: the whole conversation can be driven from the keyboard alone. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'
import { findMessage } from './findMessage'

function renderChat(): ReturnType<typeof render> {
  return render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN)} lang="en" />)
}

describe('ChatFeature keyboard traversal', () => {
  it('reaches the message field first, then Send once there is text to send', async () => {
    const user = userEvent.setup()
    renderChat()
    await findMessage('Hi! Which transaction would you like to dispute?')

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
    await findMessage('Hi! Which transaction would you like to dispute?')

    await user.tab()
    await user.keyboard('the Tienda Sol one')
    await user.tab()
    await user.keyboard('{Enter}')
    // Sending hands focus back to the field, so the next message can be typed straight away.
    expect(screen.getByLabelText('Your message')).toHaveFocus()

    // The numbered choice comes before the text field in the tab order.
    const choice = await screen.findByRole('button', {
      name: '1. MXN 250.00 at Tienda Sol on 12 June 2026',
    })
    await user.tab({ shift: true })
    expect(choice).toHaveFocus()
    await user.keyboard(' ')

    // The clicked option disappears with the next reply; focus lands on the field, not the page.
    await findMessage(/what is the reason for the dispute/i)
    expect(screen.getByLabelText('Your message')).toHaveFocus()
    await user.keyboard('unrecognized charge')
    await user.tab()
    await user.keyboard('{Enter}')

    // The yes and no replies also precede the field, yes first, and Enter on yes files the case.
    const confirm = await screen.findByRole('button', { name: 'Yes, file it' })
    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'No' })).toHaveFocus()
    await user.tab({ shift: true })
    expect(confirm).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await findMessage(/case DEMO-1234/)).toBeInTheDocument()
  })
})
