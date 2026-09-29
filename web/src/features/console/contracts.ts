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

// -----------------------------------------------------------------------------
// Ticket detail: the packet and the timeline (contracts/service_v1/handoff.py, envelope.py)
// -----------------------------------------------------------------------------

const RefPattern = /^[A-Za-z0-9_-]{1,64}$/

/** `ReasonCode` (app/domain/policy/models.py). */
export const ReasonCodeSchema = z.enum([
  'eligible',
  'product_out_of_scope',
  'transaction_type_not_disputable',
  'transaction_declined',
  'transaction_pending',
  'transaction_reversed',
  'transaction_date_in_future',
  'filing_window_expired',
  'duplicate_open_case',
  'escalate_fraud_claim',
  'escalate_low_nlu_confidence',
  'escalate_repeat_complainer',
  'escalate_amount_above_threshold',
  'escalate_amount_unknown',
  'escalate_risk_score',
])
export type ReasonCode = z.infer<typeof ReasonCodeSchema>

/** `TransactionStatus` (app/domain/policy/models.py). */
export const TransactionStatusSchema = z.enum(['Approved', 'Declined', 'Pending', 'Reversed'])
export type TransactionStatus = z.infer<typeof TransactionStatusSchema>

/** `Intent` (contracts/service_v1/envelope.py). */
export const IntentSchema = z.enum([
  'clarify',
  'present_transactions',
  'confirm_filing',
  'filing_result',
  'ineligible',
  'dispute_status',
  'policy_answer',
  'abstain',
  'refuse',
  'handoff',
  'farewell',
])
export type Intent = z.infer<typeof IntentSchema>

/** `Slot` (contracts/service_v1/envelope.py) — mirrors `customer-chat/contracts.ts`'s own
 * `SlotSchema` values; not imported from there, since that file's schema is customer-chat-scoped
 * and this one is independently versioned against the console's own contract. */
export const SlotSchema = z.enum(['transaction', 'transaction_choice', 'reason', 'confirmation'])
export type Slot = z.infer<typeof SlotSchema>

/** `Money` (contracts/service_v1/envelope.py) — `amount` is a decimal string on the wire
 * (`Money.model_dump_json()` emits `"250.00"`, never a JSON number, to keep exact precision). */
export const MoneySchema = z
  .object({
    // `decimal_places=2` on the Python side bounds the fraction at *most* two digits; it is not
    // a fixed width, and Python's own `Decimal` serialization never pads trailing zeros back in
    // (`Decimal('250')` emits `"250"`, not `"250.00"`) — confirmed against a live
    // `Money(...).model_dump_json()` call, for 0, 1 and 2 fraction digits.
    amount: z.string().regex(/^\d{1,12}(\.\d{1,2})?$/),
    currency: z.string().regex(/^[A-Z]{3}$/),
  })
  .strict()
export type Money = z.infer<typeof MoneySchema>

/** `ProductLabel` (contracts/service_v1/envelope.py) — name and the last four digits only, never
 * a full account or card number (AC-E10-05). */
export const ProductLabelSchema = z
  .object({
    name: z.string().min(1).max(60),
    last4: z.string().regex(/^\d{4}$/),
  })
  .strict()
export type ProductLabel = z.infer<typeof ProductLabelSchema>

/** `TransactionFact` (contracts/service_v1/envelope.py) — no document number field exists on this
 * contract at all; nothing here can leak one (AC-E10-05). */
export const TransactionFactSchema = z
  .object({
    ref: z.string().regex(RefPattern),
    occurred_on: z.iso.date(),
    merchant: z.string().min(1).max(80).nullable(),
    amount: MoneySchema.nullable(),
    product: ProductLabelSchema,
    status: TransactionStatusSchema,
  })
  .strict()
export type TransactionFact = z.infer<typeof TransactionFactSchema>

/** `LocalizedTitle` (contracts/service_v1/envelope.py). */
export const LocalizedTitleSchema = z
  .object({
    lang: LangSchema,
    text: z.string().min(1).max(120),
  })
  .strict()
export type LocalizedTitle = z.infer<typeof LocalizedTitleSchema>

/** `SourceRef` (contracts/service_v1/envelope.py) — one title per language; the console shows the
 * title in the *ticket's* language (the customer's own words), never the console's fixed Spanish
 * (D91 applies that fixed-Spanish rule to console chrome, not to case content in the customer's
 * own language). */
export const SourceRefSchema = z
  .object({
    section_id: z.string().regex(/^[A-Za-z0-9_.-]{1,64}$/),
    titles: z.array(LocalizedTitleSchema),
    corpus_version: z.string().min(1).max(32),
  })
  .strict()
export type SourceRef = z.infer<typeof SourceRefSchema>

/** `RiskEvidence` (contracts/service_v1/envelope.py) — AC-E10-02 requires the score, its
 * uncertainty interval and the base rate shown together; never the score alone. */
export const RiskEvidenceSchema = z
  .object({
    score: z.number().min(0).max(1),
    interval_low: z.number().min(0).max(1),
    interval_high: z.number().min(0).max(1),
    base_rate: z.number().min(0).max(1),
  })
  .strict()
export type RiskEvidence = z.infer<typeof RiskEvidenceSchema>

/** `Evidence` (contracts/service_v1/handoff.py). */
export const EvidenceSchema = z
  .object({
    reason_codes: z.array(ReasonCodeSchema),
    policy_version: z.string().min(1),
    sources: z.array(SourceRefSchema).default([]),
    risk: RiskEvidenceSchema.nullable().default(null),
  })
  .strict()
export type Evidence = z.infer<typeof EvidenceSchema>

/** `ActionRecord` (contracts/service_v1/handoff.py). */
export const ActionRecordSchema = z
  .object({
    action: z.string().min(1).max(64),
    result: z.string().min(1).max(64),
  })
  .strict()
export type ActionRecord = z.infer<typeof ActionRecordSchema>

/** `OpenQuestion` (contracts/service_v1/handoff.py). */
export const OpenQuestionSchema = z
  .object({
    slot: SlotSchema,
    attempts: z.number().int().min(0),
  })
  .strict()
export type OpenQuestion = z.infer<typeof OpenQuestionSchema>

/** `CustomerLabel` (contracts/service_v1/handoff.py) — a first name and a masked identifier
 * only, never a document number (AC-E10-05). */
export const CustomerLabelSchema = z
  .object({
    first_name: z.string().min(1).max(40),
    masked_id: z.string().regex(/^\*{4}[A-Za-z0-9]{2,4}$/),
  })
  .strict()
export type CustomerLabel = z.infer<typeof CustomerLabelSchema>

/** `HandoffPacket` (contracts/service_v1/handoff.py). */
export const HandoffPacketSchema = z
  .object({
    ticket_ref: z.string().regex(TicketRefPattern),
    reference_date: z.iso.date(),
    created_at: z.iso.datetime(),
    language: LangSchema,
    needs_language_routing: z.boolean(),
    trigger: HandoffTriggerSchema,
    customer: CustomerLabelSchema,
    category: DisputeCategorySchema.nullable().default(null),
    request_summary: z.string().min(1).max(300),
    verified_facts: z.array(TransactionFactSchema).default([]),
    actions: z.array(ActionRecordSchema).default([]),
    attempted_action: ActionRecordSchema.nullable().default(null),
    existing_case_number: z.string().regex(TicketRefPattern).nullable().default(null),
    evidence: EvidenceSchema,
    open_questions: z.array(OpenQuestionSchema).default([]),
  })
  .strict()
  // `_language_flag_follows_the_language` (contracts/service_v1/handoff.py): Portuguese and
  // English flag the packet so the console can route it — the flag is true exactly when the
  // language is not Spanish, never independently of it.
  .refine((packet) => packet.needs_language_routing === (packet.language !== 'es'), {
    message: 'needs_language_routing must be true exactly when language is not es',
  })
export type HandoffPacket = z.infer<typeof HandoffPacketSchema>

/** `TimelineEntry` (contracts/service_v1/console.py) — never message text (AC-E10-05). */
export const TimelineEntrySchema = z
  .object({
    occurred_at: z.iso.datetime(),
    trace_id: z.string().min(1).max(64),
    intent: IntentSchema,
    state_before: z.string().min(1).max(48),
    state_after: z.string().min(1).max(48),
    render_mode: z.enum(['template', 'model']),
    reason_code: ReasonCodeSchema.nullable().default(null),
    policy_version: z.string().min(1).nullable().default(null),
  })
  .strict()
export type TimelineEntry = z.infer<typeof TimelineEntrySchema>

/** `TicketDetail` (contracts/service_v1/console.py). */
export const TicketDetailSchema = z
  .object({
    item: QueueItemSchema,
    packet: HandoffPacketSchema,
    timeline: z.array(TimelineEntrySchema),
  })
  .strict()
  // `_row_describes_the_packet` (contracts/service_v1/console.py): the queue row and the packet
  // of one ticket agree on what they both state.
  .refine(
    (detail) =>
      detail.item.ticket_ref === detail.packet.ticket_ref &&
      detail.item.trigger === detail.packet.trigger &&
      detail.item.language === detail.packet.language &&
      detail.item.category === detail.packet.category &&
      detail.item.reference_date === detail.packet.reference_date &&
      detail.item.created_at === detail.packet.created_at,
    { message: 'the queue item does not describe the packet' },
  )
export type TicketDetail = z.infer<typeof TicketDetailSchema>
