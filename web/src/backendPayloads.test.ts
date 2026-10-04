/**
 * Every strict schema the client parses a response with accepts what the backend's own response
 * model produces. `test/backend-payloads.json` is generated from those models
 * (`tests/fixtures/web_payloads.py`) and a Python test keeps it current, so a field the backend
 * adds fails here instead of at runtime in a browser.
 */
import { describe, expect, it } from 'vitest'
import payloads from '../test/backend-payloads.json'
import { QueueResponseSchema, TicketDetailSchema } from './features/console/contracts'
import { TurnResponseSchema } from './features/customer-chat/contracts'
import { DemoPersonaDirectorySchema, SessionResponseSchema } from './features/sign-in/contracts'

describe('backend payloads', () => {
  it('QueueResponse parses, with and without a claim', () => {
    const queue = QueueResponseSchema.parse(payloads.QueueResponse)
    expect(queue.items.map((item) => item.claimed_by)).toEqual([null, 'AGT-1'])
  })

  it('TicketDetail parses with its notes', () => {
    const detail = TicketDetailSchema.parse(payloads.TicketDetail)
    expect(detail.item.claimed_by).toBe('AGT-1')
    expect(detail.notes).toHaveLength(1)
  })

  it('TicketDetail parses with every optional element absent', () => {
    const detail = TicketDetailSchema.parse(payloads.TicketDetailSparse)
    expect(detail.item.claimed_by).toBeNull()
    expect(detail.item.category).toBeNull()
    expect(detail.notes).toEqual([])
    expect(detail.timeline).toEqual([])
    expect(detail.packet.verified_facts[0]?.merchant).toBeNull()
  })

  it('a payload missing claimed_by or notes is refused', () => {
    const withoutClaim = structuredClone(payloads.TicketDetail) as Record<string, unknown>
    delete (withoutClaim.item as Record<string, unknown>).claimed_by
    expect(() => TicketDetailSchema.parse(withoutClaim)).toThrow()

    const withoutNotes = structuredClone(payloads.TicketDetail) as Record<string, unknown>
    delete withoutNotes.notes
    expect(() => TicketDetailSchema.parse(withoutNotes)).toThrow()
  })

  it('TurnResponse parses', () => {
    expect(TurnResponseSchema.parse(payloads.TurnResponse).next_expected).toBe('transaction_choice')
  })

  it('DemoPersonaDirectory parses', () => {
    expect(DemoPersonaDirectorySchema.parse(payloads.DemoPersonaDirectory).personas).toHaveLength(2)
  })

  it('SessionResponse parses', () => {
    expect(SessionResponseSchema.parse(payloads.SessionResponse).expires_in).toBe(1800)
  })
})
