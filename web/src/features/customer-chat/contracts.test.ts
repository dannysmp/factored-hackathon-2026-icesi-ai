/**
 * Unit tests: the three rules `contracts/service_v1/api.py` enforces that a naive Zod mirror of
 * its field shapes alone would miss — each pinned against the exact input that would otherwise
 * slip through.
 */
import { describe, expect, it } from 'vitest'
import { ChoiceSchema, TurnRequestSchema, TurnResponseSchema } from './contracts'

const BASE_RESPONSE = {
  contract_version: '1' as const,
  turn_id: 'fixture-turn-0001',
  conversation_id: 'fixture-conversation-0001',
  state_version: 1,
  lang: 'en' as const,
  reply: 'Hi!',
  reference_date_line: 'Today is Thursday, 18 June 2026.',
  demo_notice: null,
  choices: [] as unknown[],
  next_expected: null,
  end_session: false,
  handoff_ticket: null,
}

describe('ChoiceSchema', () => {
  it('accepts a plain label', () => {
    expect(ChoiceSchema.parse({ number: 1, label: 'MXN 250.00 at Tienda Sol' })).toBeTruthy()
  })

  it('refuses a label holding a control character', () => {
    expect(() => ChoiceSchema.parse({ number: 1, label: 'a\x00b' })).toThrow()
  })

  it('refuses a label holding a newline, unlike TurnRequest.text', () => {
    // Choice.label is SafeText (envelope.py's _refuse_control_characters), which has no
    // exception for `\n`; only TurnRequest.text's separate validator (api.py) allows it.
    expect(() => ChoiceSchema.parse({ number: 1, label: 'a\nb' })).toThrow()
  })

  it('refuses an unknown field (extra="forbid" on the Python side)', () => {
    expect(() => ChoiceSchema.parse({ number: 1, label: 'ok', risk_score: 0.9 })).toThrow()
  })
})

describe('TurnRequestSchema', () => {
  it('accepts ordinary text, including a line break', () => {
    expect(
      TurnRequestSchema.parse({ turn_id: 'a'.repeat(8), text: 'line one\nline two' }),
    ).toBeTruthy()
  })

  it('refuses whitespace-only text', () => {
    expect(() => TurnRequestSchema.parse({ turn_id: 'a'.repeat(8), text: '   ' })).toThrow()
  })

  it('refuses a control character other than the newline', () => {
    expect(() =>
      TurnRequestSchema.parse({ turn_id: 'a'.repeat(8), text: 'hello\x00world' }),
    ).toThrow()
  })

  it('refuses an unknown field', () => {
    expect(() =>
      TurnRequestSchema.parse({ turn_id: 'a'.repeat(8), text: 'hi', confirmed: true }),
    ).toThrow()
  })
})

describe('TurnResponseSchema', () => {
  it('accepts choices numbered from 1 without gaps', () => {
    const response = {
      ...BASE_RESPONSE,
      choices: [
        { number: 1, label: 'first' },
        { number: 2, label: 'second' },
      ],
    }
    expect(TurnResponseSchema.parse(response)).toBeTruthy()
  })

  it('refuses choices with a gap', () => {
    const response = {
      ...BASE_RESPONSE,
      choices: [
        { number: 1, label: 'first' },
        { number: 3, label: 'third' },
      ],
    }
    expect(() => TurnResponseSchema.parse(response)).toThrow()
  })

  it('refuses choices numbered out of order', () => {
    const response = {
      ...BASE_RESPONSE,
      choices: [
        { number: 2, label: 'second' },
        { number: 1, label: 'first' },
      ],
    }
    expect(() => TurnResponseSchema.parse(response)).toThrow()
  })

  it('refuses a repeated choice number', () => {
    const response = {
      ...BASE_RESPONSE,
      choices: [
        { number: 1, label: 'first' },
        { number: 1, label: 'again' },
      ],
    }
    expect(() => TurnResponseSchema.parse(response)).toThrow()
  })

  it('refuses an unknown field', () => {
    expect(() => TurnResponseSchema.parse({ ...BASE_RESPONSE, risk_score: 0.9 })).toThrow()
  })
})
