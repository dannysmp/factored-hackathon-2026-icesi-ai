/**
 * A scripted English conversation, in `TurnResponse` shape.
 *
 * Team-generated, not derived from a real customer: it exists to prove the chat UI end to end
 * (the reference-date line, the demonstration notice, numbered choices, the confirmation button,
 * a handoff ticket) before the live endpoint exists. It mirrors the shape of the "file dispute"
 * scenario recorded as raw decision envelopes (`tests/fixtures/scripted_flows.en.json`,
 * Python-side, agent-only), but carries the rendered customer-facing text those envelopes
 * deliberately do not: rendering that text is the model-renderer slice's job, not this one's.
 */
import type { TurnResponse } from './contracts'

const REFERENCE_DATE_LINE = 'Today is Thursday, 18 June 2026.'
const DEMO_NOTICE = 'This is a demonstration conversation, not your real account.'

export const FILE_DISPUTE_EN: readonly TurnResponse[] = [
  {
    contract_version: '1',
    turn_id: 'fixture-turn-0001',
    conversation_id: 'fixture-conversation-0001',
    state_version: 1,
    lang: 'en',
    reply: 'Hi! Which transaction would you like to dispute?',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: 'transaction',
    end_session: false,
    handoff_ticket: null,
    case_number: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-0002',
    conversation_id: 'fixture-conversation-0001',
    state_version: 2,
    lang: 'en',
    reply: 'I found one transaction. Is this the one?',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [{ number: 1, label: 'MXN 250.00 at Tienda Sol on 12 June 2026' }],
    next_expected: 'transaction_choice',
    end_session: false,
    handoff_ticket: null,
    case_number: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-0003',
    conversation_id: 'fixture-conversation-0001',
    state_version: 3,
    lang: 'en',
    reply:
      'What is the reason for the dispute: an unrecognized charge, a duplicate, the wrong amount, or something else?',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: 'reason',
    end_session: false,
    handoff_ticket: null,
    case_number: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-0004',
    conversation_id: 'fixture-conversation-0001',
    state_version: 4,
    lang: 'en',
    reply:
      "You're disputing MXN 250.00 at Tienda Sol on 12 June 2026 as an unrecognized charge. File this dispute?",
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: 'confirmation',
    end_session: false,
    handoff_ticket: null,
    case_number: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-0005',
    conversation_id: 'fixture-conversation-0001',
    state_version: 5,
    lang: 'en',
    reply: "Done. I've filed your dispute as case DEMO-1234. Is there anything else?",
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: null,
    end_session: false,
    handoff_ticket: null,
    case_number: 'DEMO-1234',
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-0006',
    conversation_id: 'fixture-conversation-0001',
    state_version: 6,
    lang: 'en',
    reply: 'Thanks for reaching out. Have a good day!',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: null,
    end_session: true,
    handoff_ticket: null,
    case_number: null,
  },
] as const
