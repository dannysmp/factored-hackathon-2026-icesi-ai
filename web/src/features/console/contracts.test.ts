/**
 * Unit tests: the rules a naive Zod mirror of `contracts/service_v1/console.py`'s field shapes
 * alone would miss.
 */
import { describe, expect, it } from 'vitest'
import { QueueItemSchema, QueueResponseSchema } from './contracts'

const BASE_ITEM = {
  ticket_ref: 'T-20260618-AAAAAAAA',
  trigger: 'fraud_report' as const,
  language: 'es' as const,
  status: 'open' as const,
  created_at: '2026-06-18T14:05:00Z',
  reference_date: '2026-06-18',
  promised_contact_by: '2026-06-19',
  age_days: 0,
  priority: true,
}

describe('QueueItemSchema', () => {
  it('defaults category to null when the field is missing, matching the Python default', () => {
    const item = QueueItemSchema.parse(BASE_ITEM)
    expect(item.category).toBeNull()
  })

  it('refuses an unknown field (extra="forbid" on the Python side)', () => {
    expect(() => QueueItemSchema.parse({ ...BASE_ITEM, agent_note: 'not a real field' })).toThrow()
  })

  it('refuses a trigger outside the known HandoffTrigger vocabulary', () => {
    expect(() => QueueItemSchema.parse({ ...BASE_ITEM, trigger: 'not_a_trigger' })).toThrow()
  })

  it('refuses a negative age', () => {
    expect(() => QueueItemSchema.parse({ ...BASE_ITEM, age_days: -1 })).toThrow()
  })
})

describe('QueueResponseSchema', () => {
  it('accepts an empty queue', () => {
    const response = QueueResponseSchema.parse({
      reference_date: '2026-06-18',
      reference_date_origin: 'setting',
      items: [],
    })
    expect(response.items).toEqual([])
  })

  it('refuses an unknown reference_date_origin', () => {
    expect(() =>
      QueueResponseSchema.parse({
        reference_date: '2026-06-18',
        reference_date_origin: 'guessed',
        items: [],
      }),
    ).toThrow()
  })
})
