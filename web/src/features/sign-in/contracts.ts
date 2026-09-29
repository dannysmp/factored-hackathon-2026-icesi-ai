/**
 * Demo sign-in contract, client side.
 *
 * Mirrors `app/api/demo_signin.py`'s `DemoPersonaSummary`/`DemoPersonaDirectory` and
 * `app/api/auth.py`'s `SessionResponse` field for field, the same convention
 * `customer-chat/contracts.ts` uses for the turn contract.
 */
import { z } from 'zod'

export const PersonaAudienceSchema = z.enum(['customer', 'agent'])
export type PersonaAudience = z.infer<typeof PersonaAudienceSchema>

export const DemoPersonaSummarySchema = z
  .object({
    slug: z.string().min(1),
    display_name: z.string().min(1),
    language: z.string().min(1),
    audience: PersonaAudienceSchema,
  })
  .strict()
export type DemoPersonaSummary = z.infer<typeof DemoPersonaSummarySchema>

export const DemoPersonaDirectorySchema = z
  .object({
    personas: z.array(DemoPersonaSummarySchema),
  })
  .strict()

export const SessionResponseSchema = z.object({
  access_token: z.string().min(1),
  token_type: z.string().min(1),
  expires_at: z.string().min(1),
  expires_in: z.number().int(),
})
export type SessionResponse = z.infer<typeof SessionResponseSchema>
