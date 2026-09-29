/**
 * Unit tests: the rules a naive Zod mirror of `contracts/service_v1/console.py`'s field shapes
 * alone would miss.
 */
import { describe, expect, it } from 'vitest'
import { DEMO_TICKET_DETAILS } from './fixtures'
import {
  HandoffPacketSchema,
  MoneySchema,
  QueueItemSchema,
  QueueResponseSchema,
  TicketDetailSchema,
} from './contracts'

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

  it('refuses a malformed reference_date, matching what the overdue comparison assumes', () => {
    expect(() => QueueItemSchema.parse({ ...BASE_ITEM, reference_date: '18/06/2026' })).toThrow()
  })

  it('refuses a malformed created_at', () => {
    expect(() => QueueItemSchema.parse({ ...BASE_ITEM, created_at: 'not a timestamp' })).toThrow()
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

describe('MoneySchema', () => {
  it.each(['250', '250.5', '250.00'])(
    'accepts %s, since decimal_places=2 on the Python side bounds the fraction, not pads it',
    (amount) => {
      expect(MoneySchema.parse({ amount, currency: 'MXN' }).amount).toBe(amount)
    },
  )

  it('refuses more than two fraction digits', () => {
    expect(() => MoneySchema.parse({ amount: '250.001', currency: 'MXN' })).toThrow()
  })
})

describe('HandoffPacketSchema', () => {
  const [FIRST] = DEMO_TICKET_DETAILS
  if (FIRST === undefined) {
    throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least one entry for this test')
  }

  it('accepts the fixture packet as-is', () => {
    expect(() => HandoffPacketSchema.parse(FIRST.packet)).not.toThrow()
  })

  it('refuses needs_language_routing=false when the language is not Spanish', () => {
    expect(() =>
      HandoffPacketSchema.parse({ ...FIRST.packet, language: 'en', needs_language_routing: false }),
    ).toThrow()
  })

  it('refuses needs_language_routing=true when the language is Spanish', () => {
    expect(() =>
      HandoffPacketSchema.parse({ ...FIRST.packet, language: 'es', needs_language_routing: true }),
    ).toThrow()
  })
})

describe('TicketDetailSchema', () => {
  const [FIRST] = DEMO_TICKET_DETAILS
  if (FIRST === undefined) {
    throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least one entry for this test')
  }

  it('accepts the fixture detail as-is', () => {
    expect(() => TicketDetailSchema.parse(FIRST)).not.toThrow()
  })

  it('refuses a detail whose item and packet disagree (_row_describes_the_packet)', () => {
    expect(() =>
      TicketDetailSchema.parse({
        ...FIRST,
        item: { ...FIRST.item, trigger: 'card_loss' },
      }),
    ).toThrow()
  })
})
