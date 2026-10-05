/** Test helper: a persona directory the way the sign-in screen receives it from the service. */
import type { PersonaDirectory } from './api'
import type { DemoPersonaSummary, ReferenceDateLines } from './contracts'

/** The reference-date line in each language, as the service words it for 18 June 2026. */
export const REFERENCE_DATE_LINES: ReferenceDateLines = {
  es: 'Fecha de referencia de los datos: 18 de junio de 2026',
  pt: 'Data de referência dos dados: 18 de junho de 2026',
  en: 'Reference date of the data: June 18, 2026',
}

/** A directory listing `personas`, carrying `REFERENCE_DATE_LINES` unless others are given. */
export function directoryOf(
  personas: readonly DemoPersonaSummary[],
  referenceDateLines: ReferenceDateLines = REFERENCE_DATE_LINES,
): PersonaDirectory {
  return { personas, referenceDateLines }
}
