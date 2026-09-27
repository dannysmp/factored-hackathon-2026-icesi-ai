/** Unit tests: the parts of `useConversation`'s guard that a rendered UI can't reach directly. */
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ChatClient } from './client'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'
import { useConversation } from './useConversation'

describe('useConversation', () => {
  it('ignores a second call to send while the first is still in flight', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN)
    const { result } = renderHook(() => useConversation(client))
    await waitFor(() => {
      expect(result.current.status).toBe('ready')
    })

    act(() => {
      // Two calls back to back, with no `await` between them: the second must see `status`
      // already flipped to `loading` by the first and be a no-op, not skip a scripted turn.
      result.current.send('first message')
      result.current.send('should be ignored')
    })

    await waitFor(() => {
      expect(result.current.latest?.turn_id).toBe('fixture-turn-0002')
    })
    expect(result.current.messages.filter((message) => message.from === 'customer')).toHaveLength(1)
  })
})
