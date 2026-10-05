/**
 * Demo sign-in contract, client side.
 *
 * Mirrors `app/api/demo_signin.py`'s `DemoPersonaSummary`/`DemoPersonaDirectory` and
 * `app/api/auth.py`'s `SessionResponse` field for field, the same convention
 * `customer-chat/contracts.ts` uses for the turn contract.
 */
import { z } from 'zod'

/** Which console a persona signs in to: the customer chat or the human-agent console. */
export const PersonaAudienceSchema = z.enum(['customer', 'agent'])
/** A value that passes `PersonaAudienceSchema`. */
export type PersonaAudience = z.infer<typeof PersonaAudienceSchema>

/**
 * One selectable demo persona. `language` stays a plain string because the backend's persona
 * model is not limited to the three languages this frontend displays; callers narrow it.
 */
export const DemoPersonaSummarySchema = z
  .object({
    slug: z.string().min(1),
    display_name: z.string().min(1),
    language: z.string().min(1),
    audience: PersonaAudienceSchema,
  })
  .strict()
/** A value that passes `DemoPersonaSummarySchema`. */
export type DemoPersonaSummary = z.infer<typeof DemoPersonaSummarySchema>

/** The list of personas the sign-in broker currently accepts, for every audience. */
export const DemoPersonaDirectorySchema = z
  .object({
    personas: z.array(DemoPersonaSummarySchema),
  })
  .strict()

/** The session the broker issues on a successful sign-in; only `access_token` is used. */
export const SessionResponseSchema = z.object({
  access_token: z.string().min(1),
  token_type: z.string().min(1),
  expires_at: z.string().min(1),
  expires_in: z.number().int(),
})
/** A value that passes `SessionResponseSchema`. */
export type SessionResponse = z.infer<typeof SessionResponseSchema>
