/** Component test: the chat's demonstration notice stays on screen, with no way to dismiss it. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'

const NOTICE = 'This is a demonstration conversation, not your real account.'

describe('ChatFeature demonstration notice', () => {
  it('is shown on every turn of the conversation and offers no way to dismiss it', async () => {
    const user = userEvent.setup()
    render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN)} lang="en" />)
    await screen.findByText(NOTICE)

    const noticeStaysAfter = (): void => {
      expect(screen.getByText(NOTICE)).toBeInTheDocument()
    }

    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await user.click(await screen.findByRole('button', { name: /^1\./ }))
    await screen.findByText(/what is the reason for the dispute/i)
    noticeStaysAfter()

    await user.type(screen.getByLabelText('Your message'), 'unrecognized charge')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))
    await screen.findByText(/case DEMO-1234/)
    noticeStaysAfter()

    expect(screen.getByText(NOTICE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /close|dismiss|hide|cerrar|fechar/i })).toBeNull()
  })
})
