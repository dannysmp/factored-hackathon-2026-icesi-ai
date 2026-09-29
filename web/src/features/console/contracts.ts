/**
 * Console API contract, client side.
 *
 * Mirrors `contracts/service_v1/console.py`'s `QueueFilters`/`QueueItem`/`QueueResponse` field
 * for field (customer-chat/contracts.ts's own convention): every object schema is `.strict()`,
 * matching `ContractModel`'s `extra="forbid"`.
 */
import { z } from 'zod'
import { LangSchema } from '../customer-chat/contracts'

const TicketRefPattern = /^[A-Za-z0-9_-]{1,32}$/

/** `HandoffTrigger` (contracts/service_v1/handoff.py). */
export const HandoffTriggerSchema = z.enum([
  'fraud_report',
  'card_loss',
  'customer_request',
  'amount_review',
  'repeat_complainer',
  'risk_score',
  'amount_unknown',
  'low_understanding',
  'tool_failure',
  'filing_unverified',
])
export type HandoffTrigger = z.infer<typeof HandoffTriggerSchema>

/** `DisputeCategory` (app/domain/policy/models.py). */
export const DisputeCategorySchema = z.enum([
  'unrecognized_charge',
  'duplicate_charge',
  'wrong_amount',
  'service_not_received',
  'fraud_claim',
])
export type DisputeCategory = z.infer<typeof DisputeCategorySchema>

/** `TicketStatus` (contracts/service_v1/console.py). */
export const TicketStatusSchema = z.enum(['open', 'in_review', 'resolved', 'rejected'])
export type TicketStatus = z.infer<typeof TicketStatusSchema>

/** `ReferenceDateOrigin` (contracts/service_v1/api.py). */
export const ReferenceDateOriginSchema = z.enum(['setting', 'seed', 'system'])

export const QueueItemSchema = z
  .object({
    ticket_ref: z.string().regex(TicketRefPattern),
    trigger: HandoffTriggerSchema,
    language: LangSchema,
    category: DisputeCategorySchema.nullable().default(null),
    status: TicketStatusSchema,
    created_at: z.iso.datetime(),
    // A plain ISO date (`YYYY-MM-DD`), not just a non-empty string: `QueueTable.tsx`'s own
    // overdue check compares `reference_date`/`promised_contact_by` lexicographically, which is
    // only a correct date comparison for well-formed ISO dates — the schema now enforces the
    // shape that comparison already assumed.
    reference_date: z.iso.date(),
    promised_contact_by: z.iso.date(),
    age_days: z.number().int().min(0),
    priority: z.boolean(),
  })
  .strict()
export type QueueItem = z.infer<typeof QueueItemSchema>

export const QueueResponseSchema = z
  .object({
    reference_date: z.iso.date(),
    reference_date_origin: ReferenceDateOriginSchema,
    items: z.array(QueueItemSchema),
  })
  .strict()
export type QueueResponse = z.infer<typeof QueueResponseSchema>

/** The one filter combination the queue route accepts (`QueueFilters`); both fields optional. */
export interface QueueFilters {
  language?: z.infer<typeof LangSchema>
  trigger?: HandoffTrigger
}

/** The triggers that sort first in the queue (`is_priority`, contracts/service_v1/console.py). */
export const PRIORITY_TRIGGERS: ReadonlySet<HandoffTrigger> = new Set(['fraud_report', 'card_loss'])
