/** The plain-words description of the case each demonstration persona represents. */
import type { Messages } from '../../i18n/messages'

/**
 * The catalog key that describes each persona's case, keyed by persona slug.
 *
 * The persona directory carries who a persona is, not what case they stand for, so the wording
 * lives in the message catalogs where it is translated with the rest of the screen. A persona
 * missing from this map is shown without a case line.
 */
export const PERSONA_CASE_KEYS: Readonly<Record<string, keyof Messages>> = {
  ana: 'signin.persona.ana.case',
  joao: 'signin.persona.joao.case',
  emma: 'signin.persona.emma.case',
  carlos: 'signin.persona.carlos.case',
  mariana: 'signin.persona.mariana.case',
  'agent-beatriz': 'signin.persona.agent-beatriz.case',
  'agent-diego': 'signin.persona.agent-diego.case',
}

/** The initials shown on a persona's avatar: the first letter of up to two words of the name. */
export function personaInitials(displayName: string): string {
  return displayName
    .split(/\s+/)
    .filter((word) => word !== '')
    .slice(0, 2)
    .map((word) => Array.from(word)[0]?.toLocaleUpperCase() ?? '')
    .join('')
}
