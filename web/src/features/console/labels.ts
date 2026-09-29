/**
 * Human-readable labels for the console's enum values — display only, never sent anywhere.
 *
 * Fixed Spanish literals, not a catalog entry (D91): the console stays fixed-Spanish and never
 * imports the trilingual `useT` hook chat and sign-in use — a ticket's own language (`Lang`) is
 * shown to the agent through `LANGUAGE_LABELS`, a small fixed Spanish-name lookup, not translated
 * through any per-viewer language mechanism.
 */
import type { DisputeCategory, HandoffTrigger, TicketStatus } from './contracts'
import type { Lang } from '../customer-chat/contracts'

export const TRIGGER_LABELS: Record<HandoffTrigger, string> = {
  fraud_report: 'Reporte de fraude',
  card_loss: 'Pérdida de tarjeta',
  customer_request: 'Solicitud del cliente',
  amount_review: 'Revisión de monto',
  repeat_complainer: 'Reclamante recurrente',
  risk_score: 'Puntaje de riesgo',
  amount_unknown: 'Monto desconocido',
  low_understanding: 'Comprensión baja',
  tool_failure: 'Falla de herramienta',
  filing_unverified: 'Radicación no verificada',
}

export const CATEGORY_LABELS: Record<DisputeCategory, string> = {
  unrecognized_charge: 'Cargo no reconocido',
  duplicate_charge: 'Cargo duplicado',
  wrong_amount: 'Monto incorrecto',
  service_not_received: 'Servicio no recibido',
  fraud_claim: 'Reclamo de fraude',
}

export const STATUS_LABELS: Record<TicketStatus, string> = {
  open: 'Abierto',
  in_review: 'En revisión',
  resolved: 'Resuelto',
  rejected: 'Rechazado',
}

export const LANGUAGE_LABELS: Record<Lang, string> = {
  es: 'Español',
  pt: 'Portugués',
  en: 'Inglés',
}
