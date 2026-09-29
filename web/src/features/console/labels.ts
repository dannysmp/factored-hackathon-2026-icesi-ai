/**
 * Human-readable labels for the console's enum values — display only, never sent anywhere.
 *
 * Fixed Spanish literals, not a catalog entry (D91): the console stays fixed-Spanish and never
 * imports the trilingual `useT` hook chat and sign-in use — a ticket's own language (`Lang`) is
 * shown to the agent through `LANGUAGE_LABELS`, a small fixed Spanish-name lookup, not translated
 * through any per-viewer language mechanism.
 */
import type {
  DisputeCategory,
  HandoffTrigger,
  Intent,
  ReasonCode,
  Slot,
  TicketStatus,
  TransactionStatus,
} from './contracts'
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

/** `ReasonCode` (app/domain/policy/models.py) — every code the policy engine can return, shown
 * verbatim to the agent (AC-E10-02: "evidence: reason codes"). */
export const REASON_CODE_LABELS: Record<ReasonCode, string> = {
  eligible: 'Elegible',
  product_out_of_scope: 'Producto fuera de alcance',
  transaction_type_not_disputable: 'Tipo de transacción no disputable',
  transaction_declined: 'Transacción declinada',
  transaction_pending: 'Transacción pendiente',
  transaction_reversed: 'Transacción reversada',
  transaction_date_in_future: 'Fecha de transacción futura',
  filing_window_expired: 'Ventana de radicación vencida',
  duplicate_open_case: 'Caso abierto duplicado',
  escalate_fraud_claim: 'Escalado: reclamo de fraude',
  escalate_low_nlu_confidence: 'Escalado: baja confianza de comprensión',
  escalate_repeat_complainer: 'Escalado: reclamante recurrente',
  escalate_amount_above_threshold: 'Escalado: monto sobre el umbral',
  escalate_amount_unknown: 'Escalado: monto desconocido',
  escalate_risk_score: 'Escalado: puntaje de riesgo',
}

/** `TransactionStatus` (app/domain/policy/models.py) — the source's own transaction status. */
export const TRANSACTION_STATUS_LABELS: Record<TransactionStatus, string> = {
  Approved: 'Aprobada',
  Declined: 'Declinada',
  Pending: 'Pendiente',
  Reversed: 'Reversada',
}

/** `Intent` (contracts/service_v1/envelope.py) — the timeline's own step label. */
export const INTENT_LABELS: Record<Intent, string> = {
  clarify: 'Aclaración',
  present_transactions: 'Transacciones presentadas',
  confirm_filing: 'Confirmación de radicación',
  filing_result: 'Resultado de radicación',
  ineligible: 'No elegible',
  dispute_status: 'Estado del caso',
  policy_answer: 'Respuesta de política',
  abstain: 'Abstención',
  refuse: 'Rechazo',
  handoff: 'Traspaso a un agente',
  farewell: 'Cierre de la conversación',
}

/** `Slot` (contracts/service_v1/envelope.py) — what an open question is still waiting for. */
export const SLOT_LABELS: Record<Slot, string> = {
  transaction: 'La transacción',
  transaction_choice: 'Cuál transacción',
  reason: 'El motivo del reclamo',
  confirmation: 'La confirmación',
}
