/**
 * A scripted queue snapshot for the console's fixture client, shaped like a real
 * `GET /v1/agent/queue` response (priority tickets first) — one fraud ticket, one Portuguese
 * ticket among them, and a mix of ages and categories to exercise the table's own range of values.
 * Also holds the empty queue and two ticket details, used by the fixture clients and tests. Ticket references and customer data are invented; no document number appears.
 */
import type { QueueItem, QueueResponse, TicketDetail } from './contracts'

/** The demo queue: five tickets, two of them priority, dated against the reference day 2026-06-18. */
export const DEMO_QUEUE: QueueResponse = {
  reference_date: '2026-06-18',
  reference_date_origin: 'setting',
  items: [
    {
      ticket_ref: 'T-20260618-AAAAAAAA',
      trigger: 'fraud_report',
      language: 'es',
      category: 'fraud_claim',
      status: 'open',
      created_at: '2026-06-18T14:05:00Z',
      reference_date: '2026-06-18',
      promised_contact_by: '2026-06-19',
      age_days: 0,
      priority: true,
      claimed_by: null,
    },
    {
      ticket_ref: 'T-20260617-BBBBBBBB',
      trigger: 'card_loss',
      language: 'pt',
      category: null,
      status: 'open',
      created_at: '2026-06-17T09:30:00Z',
      reference_date: '2026-06-18',
      promised_contact_by: '2026-06-18',
      age_days: 1,
      priority: true,
      claimed_by: null,
    },
    {
      ticket_ref: 'T-20260616-CCCCCCCC',
      trigger: 'customer_request',
      language: 'en',
      category: 'duplicate_charge',
      status: 'in_review',
      created_at: '2026-06-16T18:15:00Z',
      reference_date: '2026-06-18',
      promised_contact_by: '2026-06-20',
      age_days: 2,
      priority: false,
      claimed_by: null,
    },
    {
      ticket_ref: 'T-20260615-DDDDDDDD',
      trigger: 'amount_review',
      language: 'pt',
      category: 'wrong_amount',
      status: 'open',
      created_at: '2026-06-15T11:45:00Z',
      reference_date: '2026-06-18',
      promised_contact_by: '2026-06-21',
      age_days: 3,
      priority: false,
      claimed_by: null,
    },
    {
      ticket_ref: 'T-20260614-EEEEEEEE',
      trigger: 'repeat_complainer',
      language: 'es',
      category: 'unrecognized_charge',
      status: 'open',
      created_at: '2026-06-14T08:00:00Z',
      reference_date: '2026-06-18',
      promised_contact_by: '2026-06-22',
      age_days: 4,
      priority: false,
      claimed_by: null,
    },
  ],
}

/** A queue with no open tickets: the console's empty state. */
export const EMPTY_QUEUE: QueueResponse = {
  reference_date: '2026-06-18',
  reference_date_origin: 'setting',
  items: [],
}

/** The `DEMO_QUEUE` row with this reference; throws if the fixture has none. */
function queueItem(ticketRef: string): QueueItem {
  const item = DEMO_QUEUE.items.find((candidate) => candidate.ticket_ref === ticketRef)
  if (item === undefined) {
    throw new Error(`no such fixture queue item: ${ticketRef}`)
  }
  return item
}

/**
 * Two ticket details, one Spanish and one English (`TicketDetail._row_describes_the_packet`
 * requires each packet to agree with its own queue row on ticket_ref/trigger/language/category/
 * reference_date/created_at, so each one reuses its exact `DEMO_QUEUE` item rather than
 * restating those fields and risking drift). The English one's own source title and request
 * summary stay in English — the ticket's own language, not the console's fixed Spanish — the
 * same distinction `SourceRef` draws for a reply's citation. The Spanish one carries a risk
 * score; the English one has none, an open question and an attempted action.
 */
export const DEMO_TICKET_DETAILS: readonly TicketDetail[] = [
  {
    item: queueItem('T-20260618-AAAAAAAA'),
    packet: {
      ticket_ref: 'T-20260618-AAAAAAAA',
      reference_date: '2026-06-18',
      created_at: '2026-06-18T14:05:00Z',
      language: 'es',
      needs_language_routing: false,
      trigger: 'fraud_report',
      customer: { first_name: 'Ana', masked_id: '****34' },
      category: 'fraud_claim',
      request_summary: 'La clienta reporta un cargo que no reconoce y sospecha fraude.',
      verified_facts: [
        {
          ref: 'txn-8841',
          occurred_on: '2026-06-17',
          merchant: 'Tienda Sol',
          amount: { amount: '250.00', currency: 'MXN' },
          product: { name: 'Tarjeta de crédito', last4: '4321' },
          status: 'Approved',
        },
      ],
      actions: [{ action: 'evaluate_dispute', result: 'escalate_fraud_claim' }],
      attempted_action: null,
      existing_case_number: null,
      evidence: {
        reason_codes: ['escalate_fraud_claim'],
        policy_version: '2',
        sources: [
          {
            section_id: 'fraud.reporting-window',
            titles: [
              { lang: 'es', text: 'Ventana para reportar fraude' },
              { lang: 'pt', text: 'Prazo para reportar fraude' },
              { lang: 'en', text: 'Fraud reporting window' },
            ],
            corpus_version: '5',
          },
        ],
        risk: {
          score: 0.82,
          interval_low: 0.74,
          interval_high: 0.89,
          base_rate: 0.03,
          threshold: 0.7,
        },
      },
      open_questions: [],
    },
    timeline: [
      {
        occurred_at: '2026-06-18T14:03:00Z',
        trace_id: 'trace-0001',
        intent: 'present_transactions',
        state_before: 'started',
        state_after: 'clarifying',
        render_mode: 'template',
        reason_code: null,
        policy_version: null,
      },
      {
        occurred_at: '2026-06-18T14:05:00Z',
        trace_id: 'trace-0002',
        intent: 'handoff',
        state_before: 'clarifying',
        state_after: 'handed_off',
        render_mode: 'template',
        reason_code: 'escalate_fraud_claim',
        policy_version: '2',
      },
    ],
    notes: [],
  },
  {
    item: queueItem('T-20260616-CCCCCCCC'),
    packet: {
      ticket_ref: 'T-20260616-CCCCCCCC',
      reference_date: '2026-06-18',
      created_at: '2026-06-16T18:15:00Z',
      language: 'en',
      needs_language_routing: true,
      trigger: 'customer_request',
      customer: { first_name: 'Emma', masked_id: '****91' },
      category: 'duplicate_charge',
      request_summary: 'The customer sees the same charge twice and wants one of them reversed.',
      verified_facts: [
        {
          ref: 'txn-9012',
          occurred_on: '2026-06-15',
          merchant: 'City Market',
          amount: { amount: '48.50', currency: 'USD' },
          product: { name: 'Debit card', last4: '7788' },
          status: 'Approved',
        },
        {
          ref: 'txn-9013',
          occurred_on: '2026-06-15',
          merchant: 'City Market',
          amount: { amount: '48.50', currency: 'USD' },
          product: { name: 'Debit card', last4: '7788' },
          status: 'Approved',
        },
      ],
      actions: [],
      attempted_action: { action: 'create_dispute_case', result: 'confirmation_required' },
      existing_case_number: null,
      evidence: {
        reason_codes: ['escalate_low_nlu_confidence'],
        policy_version: '2',
        sources: [
          {
            section_id: 'duplicate.evidence-required',
            titles: [
              { lang: 'es', text: 'Evidencia requerida para cargos duplicados' },
              { lang: 'pt', text: 'Evidência exigida para cobranças duplicadas' },
              { lang: 'en', text: 'Evidence required for duplicate charges' },
            ],
            corpus_version: '5',
          },
        ],
        risk: null,
      },
      open_questions: [{ slot: 'reason', attempts: 2 }],
    },
    timeline: [
      {
        occurred_at: '2026-06-16T18:12:00Z',
        trace_id: 'trace-1001',
        intent: 'present_transactions',
        state_before: 'started',
        state_after: 'clarifying',
        render_mode: 'template',
        reason_code: null,
        policy_version: null,
      },
      {
        occurred_at: '2026-06-16T18:15:00Z',
        trace_id: 'trace-1002',
        intent: 'handoff',
        state_before: 'clarifying',
        state_after: 'handed_off',
        render_mode: 'model',
        reason_code: 'escalate_low_nlu_confidence',
        policy_version: '2',
      },
    ],
    notes: [],
  },
]
