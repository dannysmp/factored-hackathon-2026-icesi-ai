/** Unit test: which outcome the identifiers describe. */
import { describe, expect, it } from 'vitest'
import { resultOf } from './result'

describe('resultOf', () => {
  it('reads a case number as a filed dispute', () => {
    expect(resultOf('D-2001', null)).toEqual({
      variant: 'filed',
      reference: 'D-2001',
      filedEarlier: null,
    })
  })

  it('leads with a hand-off reference and keeps the case filed before it', () => {
    expect(resultOf('D-2001', 'DEMO-1234')).toEqual({
      variant: 'escalated',
      reference: 'DEMO-1234',
      filedEarlier: 'D-2001',
    })
  })

  it('reads only a hand-off reference as an escalation', () => {
    expect(resultOf(null, 'DEMO-1234')).toEqual({
      variant: 'escalated',
      reference: 'DEMO-1234',
      filedEarlier: null,
    })
  })

  it('reads neither as a closed conversation with nothing to quote', () => {
    expect(resultOf(null, null)).toEqual({
      variant: 'closed',
      reference: null,
      filedEarlier: null,
    })
  })
})
