/**
 * A scripted queue snapshot for the console's fixture client, shaped like a real
 * `GET /v1/agent/queue` response (priority tickets first) — one fraud ticket, one Portuguese
 * ticket among them (AC-E10-04), and a mix of ages and categories to exercise the table's own
 * range of values.
 */
import type { QueueResponse } from './contracts'

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
    },
  ],
}

/** A queue with no open tickets: the console's empty state (AC-E10-18). */
export const EMPTY_QUEUE: QueueResponse = {
  reference_date: '2026-06-18',
  reference_date_origin: 'setting',
  items: [],
}
