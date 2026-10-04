/** Component test: the chat's demonstration notice stays on screen, with no way to dismiss it. */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'

const NOTICE = 'This is a demonstration conversation, not your real account.'

describe('ChatFeature demonstration notice', () => {
  it('is shown after every turn of the conversation and holds no control that could dismiss it', async () => {
    const user = userEvent.setup()
    render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN)} lang="en" />)

    const noticeStays = (): void => {
      const note = screen.getByRole('note')
      expect(within(note).getByText(NOTICE)).toBeInTheDocument()
      expect(within(note).queryAllByRole('button')).toHaveLength(0)
    }

    await screen.findByText(NOTICE)
    noticeStays()

    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText(/I found one transaction/i)
    noticeStays()
    await user.click(await screen.findByRole('button', { name: /^1\./ }))
    await screen.findByText(/what is the reason for the dispute/i)
    noticeStays()

    await user.type(screen.getByLabelText('Your message'), 'unrecognized charge')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('button', { name: 'Confirm' })
    noticeStays()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await screen.findByText(/case DEMO-1234/)
    noticeStays()
  })
})
