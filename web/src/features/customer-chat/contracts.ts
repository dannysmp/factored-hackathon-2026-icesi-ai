/**
 * Customer API contract, client side.
 *
 * Mirrors `contracts/service_v1/api.py`'s `TurnRequest`/`TurnResponse` field for field, so the
 * two sides can only drift apart if someone edits one without the other. Validated at runtime
 * with Zod (frontend standard, section 3): a backend change surfaces here as a rejected
 * response, not as `undefined is not a function` three components away. Every object schema is
 * `.strict()`, matching `ContractModel`'s `extra="forbid"`: an unknown field is a contract
 * violation, not a value to silently ignore.
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

// Unicode category Cc ("control"), the same set `unicodedata.category(char) == "Cc"` names on
// the Python side; `\n` is allowed there and here, everything else in the category is not.
const CONTROL_CHARACTER = /\p{Cc}/u

function hasDisallowedControlCharacter(value: string): boolean {
  for (const char of value) {
    if (char !== '\n' && CONTROL_CHARACTER.test(char)) {
      return true
    }
  }
  return false
}

/** `SafeText` (envelope.py): refuses a control character; blank text is a separate rule. */
function refuseControlCharacters<Schema extends z.ZodString>(schema: Schema) {
  return schema.refine((value) => !hasDisallowedControlCharacter(value), {
    message: 'text must not contain a control character',
  })
}

export const ChoiceSchema = z
  .object({
    number: z.number().int().min(1).max(5),
    label: refuseControlCharacters(z.string().min(1).max(200)),
  })
  .strict()
export type Choice = z.infer<typeof ChoiceSchema>

export const TurnRequestSchema = z
  .object({
    turn_id: z.string().regex(IdentifierPattern),
    // `_refuse_blank_or_control` (api.py): blank (after trimming) or a control character.
    text: refuseControlCharacters(z.string().min(1).max(2000)).refine(
      (value) => value.trim() !== '',
      { message: 'text must not be blank' },
    ),
  })
  .strict()
export type TurnRequest = z.infer<typeof TurnRequestSchema>

export const TurnResponseSchema = z
  .object({
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
  .strict()
  // `_choices_are_numbered_from_one` (api.py): choices are numbered 1, 2, ... in order, so a
  // number picks exactly one; a gap, a repeat or an out-of-order list is a contract violation.
  .refine((data) => data.choices.every((choice, index) => choice.number === index + 1), {
    message: 'choices must be numbered from 1 without gaps or repeats',
    path: ['choices'],
  })
export type TurnResponse = z.infer<typeof TurnResponseSchema>

/** The fixed text a click on the confirmation button sends (AC-E10-13, AC-E5-20's typed "yes"). */
export const CONFIRMATION_TEXT = 'yes'
