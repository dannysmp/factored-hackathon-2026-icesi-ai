/**
 * Customer API contract, client side.
 *
 * Mirrors `contracts/service_v1/api.py`'s `TurnRequest`/`TurnResponse` field for field, so the
 * two sides can only drift apart if someone edits one without the other. Validated at runtime
 * with Zod (frontend standard, section 3): a backend change surfaces here as a rejected
 * response, not as `undefined is not a function` three components away.
 *
 * The response never carries the envelope, a reason code, a numeric routing input or a risk
 * score (api.py's own design principle) — only what a customer may see.
 */
import { z } from 'zod'

export const LANGUAGES = ['es', 'pt', 'en'] as const
export const LangSchema = z.enum(LANGUAGES)
export type Lang = z.infer<typeof LangSchema>

/** The element the conversation is waiting for (`contracts/service_v1/envelope.py`'s `Slot`). */
export const SlotSchema = z.enum(['transaction', 'transaction_choice', 'reason', 'confirmation'])
export type Slot = z.infer<typeof SlotSchema>

const IdentifierPattern = /^[A-Za-z0-9_-]{8,64}$/
const TicketPattern = /^[A-Za-z0-9_-]{1,32}$/

export const ChoiceSchema = z.object({
  number: z.number().int().min(1).max(5),
  label: z.string().min(1).max(200),
})
export type Choice = z.infer<typeof ChoiceSchema>

export const TurnRequestSchema = z.object({
  turn_id: z.string().regex(IdentifierPattern),
  text: z.string().min(1).max(2000),
})
export type TurnRequest = z.infer<typeof TurnRequestSchema>

export const TurnResponseSchema = z.object({
  contract_version: z.literal('1'),
  turn_id: z.string().regex(IdentifierPattern),
  conversation_id: z.string().min(1).max(64),
  state_version: z.number().int().min(1),
  lang: LangSchema,
  reply: z.string().min(1).max(2000),
  reference_date_line: z.string().min(1).max(120),
  demo_notice: z.string().min(1).max(200).nullable().default(null),
  choices: z.array(ChoiceSchema).max(5).default([]),
  next_expected: SlotSchema.nullable().default(null),
  end_session: z.boolean().default(false),
  handoff_ticket: z.string().regex(TicketPattern).nullable().default(null),
})
export type TurnResponse = z.infer<typeof TurnResponseSchema>

/** The fixed text a click on the confirmation button sends (AC-E10-13, AC-E5-20's typed "yes"). */
export const CONFIRMATION_TEXT = 'yes'
