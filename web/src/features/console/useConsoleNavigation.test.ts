/** Unit test: the selected ticket and the session are independent state, so a session change never clears the selection. */
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useConsoleNavigation } from './useConsoleNavigation'

describe('useConsoleNavigation', () => {
  it('keeps the selected ticket through a session going null then a fresh value', () => {
    const { result } = renderHook(() => useConsoleNavigation())

    act(() => {
      result.current.setSession({ token: 'token-1' })
    })
    act(() => {
      result.current.setSelectedTicketRef('T-20260618-AAAAAAAA')
    })
    expect(result.current.selectedTicketRef).toBe('T-20260618-AAAAAAAA')

    // Simulates a session expiring; this proves the *state* is independent, not what triggers
    // the expiry.
    act(() => {
      result.current.setSession(null)
    })
    expect(result.current.selectedTicketRef).toBe('T-20260618-AAAAAAAA')

    // Simulates the agent signing in again.
    act(() => {
      result.current.setSession({ token: 'token-2' })
    })
    expect(result.current.selectedTicketRef).toBe('T-20260618-AAAAAAAA')
  })

  it('clears the selected ticket only when explicitly told to (the back action)', () => {
    const { result } = renderHook(() => useConsoleNavigation())

    act(() => {
      result.current.setSelectedTicketRef('T-20260618-AAAAAAAA')
    })
    act(() => {
      result.current.setSelectedTicketRef(null)
    })

    expect(result.current.selectedTicketRef).toBeNull()
  })
})
