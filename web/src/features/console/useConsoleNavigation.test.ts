/** Unit test: the state-independence property AC-E10-08 rests on. */
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useConsoleNavigation } from './useConsoleNavigation'

describe('useConsoleNavigation', () => {
  it('keeps the selected ticket through a session going null then a fresh value (AC-E10-08)', () => {
    const { result } = renderHook(() => useConsoleNavigation())

    act(() => {
      result.current.setSession({ token: 'token-1' })
    })
    act(() => {
      result.current.setSelectedTicketRef('T-20260618-AAAAAAAA')
    })
    expect(result.current.selectedTicketRef).toBe('T-20260618-AAAAAAAA')

    // Simulates a session expiring — no such mechanism is wired anywhere in `web/` yet; this
    // proves the *state*, not the trigger, which is a separate, disclosed gap.
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
