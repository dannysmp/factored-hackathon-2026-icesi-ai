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

  describe('when a message cannot be sent', () => {
    function failingOnce(): { client: ChatClient; sent: { text: string; turnId?: string }[] } {
      const fixture = new FixtureChatClient(FILE_DISPUTE_EN)
      const sent: { text: string; turnId?: string }[] = []
      let calls = 0
      const client: ChatClient = {
        start: () => fixture.start(),
        sendTurn: (text, turnId) => {
          sent.push({ text, turnId })
          calls += 1
          return calls === 1 ? Promise.reject(new Error('offline')) : fixture.sendTurn()
        },
      }
      return { client, sent }
    }

    it('keeps the message, marks it failed, and sends it again under the same id on retry', async () => {
      const { client, sent } = failingOnce()
      const { result } = renderHook(() => useConversation(client))
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })

      act(() => {
        result.current.send('the Tienda Sol one')
      })
      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })
      const failed = result.current.messages.find((message) => message.from === 'customer')
      expect(failed).toMatchObject({ text: 'the Tienda Sol one', failed: true })

      act(() => {
        result.current.retry()
      })
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })

      expect(sent).toHaveLength(2)
      expect(sent[0]?.turnId).toBeDefined()
      expect(sent[1]).toEqual(sent[0])
      const customerMessages = result.current.messages.filter((m) => m.from === 'customer')
      expect(customerMessages).toHaveLength(1)
      expect(customerMessages[0]?.failed).toBe(false)
    })

    it('drops the failed message when the customer sends something else', async () => {
      const { client } = failingOnce()
      const { result } = renderHook(() => useConversation(client))
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })
      act(() => {
        result.current.send('the first attempt')
      })
      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })

      act(() => {
        result.current.send('something else')
      })
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })

      const texts = result.current.messages.filter((m) => m.from === 'customer').map((m) => m.text)
      expect(texts).toEqual(['something else'])
    })

    it('does nothing on retry when no message failed', async () => {
      const { client, sent } = failingOnce()
      const { result } = renderHook(() => useConversation(client))
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })

      act(() => {
        result.current.retry()
      })

      expect(sent).toHaveLength(0)
      expect(result.current.status).toBe('ready')
    })

    it('ignores a second retry while the first is still in flight', async () => {
      const { client, sent } = failingOnce()
      const { result } = renderHook(() => useConversation(client))
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })
      act(() => {
        result.current.send('the first attempt')
      })
      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })

      act(() => {
        result.current.retry()
        result.current.retry()
      })
      await waitFor(() => {
        expect(result.current.status).toBe('ready')
      })

      expect(sent).toHaveLength(2)
    })
  })

  it('gives every message its own turn id', async () => {
    const fixture = new FixtureChatClient(FILE_DISPUTE_EN)
    const turnIds: (string | undefined)[] = []
    const client: ChatClient = {
      start: () => fixture.start(),
      sendTurn: (_text, turnId) => {
        turnIds.push(turnId)
        return fixture.sendTurn()
      },
    }
    const { result } = renderHook(() => useConversation(client))
    await waitFor(() => {
      expect(result.current.status).toBe('ready')
    })

    act(() => {
      result.current.send('one')
    })
    await waitFor(() => {
      expect(result.current.latest?.turn_id).toBe('fixture-turn-0002')
    })
    act(() => {
      result.current.send('two')
    })
    await waitFor(() => {
      expect(result.current.latest?.turn_id).toBe('fixture-turn-0003')
    })

    expect(turnIds[0]).toBeDefined()
    expect(turnIds[0]).not.toBe(turnIds[1])
  })
})
