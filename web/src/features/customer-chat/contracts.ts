/**
 * Customer API contract, client side.
 *
 * Mirrors `contracts/service_v1/api.py`'s `TurnRequest`/`TurnResponse` field for field, so the
 * two sides can only drift apart if someone edits one without the other. Validated at runtime
 * with Zod: a backend change surfaces here as a rejected
 * response, not as `undefined is not a function` three components away. Every object schema is
 * `.strict()`, matching `ContractModel`'s `extra="forbid"`: an unknown field is a contract
 * violation, not a value to silently ignore.
 *
 * The response never carries the envelope, a reason code, a numeric routing input or a risk
 * score (api.py's own design principle) — only what a customer may see.
 */
import { z } from 'zod'

/** The languages a conversation can be in; the same three the backend supports. */
export const LANGUAGES = ['es', 'pt', 'en'] as const
/** Runtime validator for a language code. */
export const LangSchema = z.enum(LANGUAGES)
/** A value that passes `LangSchema`. */
export type Lang = z.infer<typeof LangSchema>

/** The element the conversation is waiting for (`contracts/service_v1/envelope.py`'s `Slot`). */
export const SlotSchema = z.enum(['transaction', 'transaction_choice', 'reason', 'confirmation'])
/** A value that passes `SlotSchema`. */
export type Slot = z.infer<typeof SlotSchema>

/** The shape of a client-chosen turn id (a UUID fits). */
const IdentifierPattern = /^[A-Za-z0-9_-]{8,64}$/
/** The shape of a human-handoff case reference. */
const TicketPattern = /^[A-Za-z0-9_-]{1,32}$/

// Unicode category Cc ("control"), the same set `unicodedata.category(char) == "Cc"` names on
// the Python side.
const CONTROL_CHARACTER = /\p{Cc}/u

/** True when `value` holds a Unicode control character, optionally tolerating `\n`. */
function hasControlCharacter(value: string, { allowNewline }: { allowNewline: boolean }): boolean {
  for (const char of value) {
    if (allowNewline && char === '\n') {
      continue
    }
    if (CONTROL_CHARACTER.test(char)) {
      return true
    }
  }
  return false
}

/** `SafeText` (envelope.py's `_refuse_control_characters`): no control character, not even `\n`. */
function refuseControlCharacters<Schema extends z.ZodString>(schema: Schema) {
  return schema.refine((value) => !hasControlCharacter(value, { allowNewline: false }), {
    message: 'text must not contain a control character',
  })
}

/** `_refuse_blank_or_control` (api.py): blank (after trimming), or a control character other
 * than `\n` — the one field this exception is for; `SafeText` above has no such exception. */
function refuseBlankOrControl<Schema extends z.ZodString>(schema: Schema) {
  return schema
    .refine((value) => value.trim() !== '', { message: 'text must not be blank' })
    .refine((value) => !hasControlCharacter(value, { allowNewline: true }), {
      message: 'text must not contain a control character',
    })
}

/** One numbered option the assistant offers; the customer may answer with its number. */
export const ChoiceSchema = z
  .object({
    number: z.number().int().min(1).max(5),
    label: refuseControlCharacters(z.string().min(1).max(200)),
  })
  .strict()
/** A value that passes `ChoiceSchema`. */
export type Choice = z.infer<typeof ChoiceSchema>

/** The longest customer message the turn endpoint accepts, in characters. */
export const MAX_TURN_TEXT_LENGTH = 2000

/** The longest assistant reply a turn response may carry, in characters. */
export const MAX_REPLY_LENGTH = 2000

/** What the client sends for one customer message. */
export const TurnRequestSchema = z
  .object({
    turn_id: z.string().regex(IdentifierPattern),
    text: refuseBlankOrControl(z.string().min(1).max(MAX_TURN_TEXT_LENGTH)),
  })
  .strict()
/** A value that passes `TurnRequestSchema`. */
export type TurnRequest = z.infer<typeof TurnRequestSchema>

/**
 * What the server returns for one turn: the reply, its language, the options on offer, the
 * element the conversation is waiting for, and whether the conversation has ended (with the
 * case reference when a human takes over). Absent optional fields take their quiet defaults.
 */
export const TurnResponseSchema = z
  .object({
    contract_version: z.literal('1'),
    turn_id: z.string().regex(IdentifierPattern),
    conversation_id: z.string().min(1).max(64),
    state_version: z.number().int().min(1),
    lang: LangSchema,
    reply: z.string().min(1).max(MAX_REPLY_LENGTH),
    reference_date_line: z.string().min(1).max(120),
    demo_notice: z.string().min(1).max(200).nullable().default(null),
    choices: z.array(ChoiceSchema).max(5).default([]),
    next_expected: SlotSchema.nullable().default(null),
    end_session: z.boolean().default(false),
    handoff_ticket: z.string().regex(TicketPattern).nullable().default(null),
    case_number: z.string().regex(TicketPattern).nullable().default(null),
  })
  .strict()
  // `_choices_are_numbered_from_one` (api.py): choices are numbered 1, 2, ... in order, so a
  // number picks exactly one; a gap, a repeat or an out-of-order list is a contract violation.
  .refine((data) => data.choices.every((choice, index) => choice.number === index + 1), {
    message: 'choices must be numbered from 1 without gaps or repeats',
    path: ['choices'],
  })
/** A value that passes `TurnResponseSchema`. */
export type TurnResponse = z.infer<typeof TurnResponseSchema>

/** The fixed text the confirmation button sends: exactly what a customer typing "yes" would send. */
export const CONFIRMATION_TEXT = 'yes'
