/**
 * Human-readable labels for the console's enum values — display only, never sent anywhere.
 *
 * Fixed Spanish literals, not catalog entries: the console is deliberately fixed-Spanish and never
 * uses the per-language catalogs or the trilingual `useT` hook that chat and sign-in use. A case's
 * own language (`Lang`) is shown to the agent through `LANGUAGE_LABELS`, a small fixed
 * Spanish-name lookup. Lookups for values the backend may extend fall back to `humanize`.
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

/** Why a conversation was handed to a person, one label per `HandoffTrigger`. */
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
  filing_unverified: 'Registro del caso sin verificar',
}

/** The kind of dispute the customer reported, one label per `DisputeCategory`. */
export const CATEGORY_LABELS: Record<DisputeCategory, string> = {
  unrecognized_charge: 'Cargo no reconocido',
  duplicate_charge: 'Cargo duplicado',
  wrong_amount: 'Monto incorrecto',
  service_not_received: 'Servicio no recibido',
  fraud_claim: 'Reclamo de fraude',
}

/** A ticket's lifecycle state, one label per `TicketStatus`. */
export const STATUS_LABELS: Record<TicketStatus, string> = {
  open: 'Abierto',
  in_review: 'En revisión',
  resolved: 'Resuelto',
  rejected: 'Rechazado',
}

/** The Spanish name of the language a case was conducted in. */
export const LANGUAGE_LABELS: Record<Lang, string> = {
  es: 'Español',
  pt: 'Portugués',
  en: 'Inglés',
}

/** `ReasonCode` (app/domain/policy/models.py) — every code the policy engine can return, shown
 * to the agent as the packet's evidence. */
export const REASON_CODE_LABELS: Record<ReasonCode, string> = {
  eligible: 'Elegible',
  product_out_of_scope: 'Producto fuera de alcance',
  transaction_type_not_disputable: 'Tipo de transacción no disputable',
  transaction_declined: 'Transacción declinada',
  transaction_pending: 'Transacción pendiente',
  transaction_reversed: 'Transacción reversada',
  transaction_date_in_future: 'Fecha de transacción futura',
  filing_window_expired: 'Plazo para disputar vencido',
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
  confirm_filing: 'Confirmación de la disputa',
  filing_result: 'Resultado de la disputa',
  ineligible: 'No elegible',
  dispute_status: 'Estado del caso',
  policy_answer: 'Respuesta de política',
  abstain: 'Sin respuesta, fuera de alcance',
  refuse: 'Solicitud rechazada',
  handoff: 'Derivación a un agente',
  farewell: 'Cierre de la conversación',
}

/** `Slot` (contracts/service_v1/envelope.py) — what an open question is still waiting for. */
export const SLOT_LABELS: Record<Slot, string> = {
  transaction: 'La transacción',
  transaction_choice: 'Cuál transacción',
  reason: 'El motivo del reclamo',
  confirmation: 'La confirmación',
}

/** What the system said, in the agent's words, for each reason a conversation was sent to a person.
 * The packet carries one fixed English sentence per reason; the console shows this Spanish one so
 * the agent never reads English inside Spanish chrome. */
export const REQUEST_SUMMARY_LABELS: Record<HandoffTrigger, string> = {
  fraud_report: 'El cliente reportó un posible fraude.',
  card_loss: 'El cliente reportó la pérdida o el robo de su tarjeta.',
  customer_request: 'El cliente pidió hablar con una persona.',
  amount_review: 'Para registrar la disputa hay que revisar el monto de la transacción.',
  repeat_complainer: 'La disputa marcó al cliente como reclamante recurrente.',
  risk_score: 'El modelo de riesgo marcó la disputa para revisión.',
  amount_unknown: 'No fue posible confirmar el monto de la transacción de la disputa.',
  low_understanding: 'La conversación no logró identificar lo que el cliente necesita.',
  tool_failure: 'Una herramienta del sistema no estuvo disponible al atender la solicitud.',
  filing_unverified: 'No se pudo confirmar el registro de la disputa después de crearlo.',
}

/** The stages a conversation moves through, as the timeline reports them. */
export const PHASE_LABELS: Record<string, string> = {
  started: 'Iniciada',
  clarifying: 'En aclaración',
  confirming: 'Esperando confirmación',
  closed: 'Cerrada',
  handed_off: 'Derivada a un agente',
  abandoned: 'Abandonada',
}

/** The steps the assistant takes and records, as the packet lists them. */
export const ACTION_LABELS: Record<string, string> = {
  turn_cap: 'Límite de turnos',
  llm_understand: 'Comprensión del mensaje',
  evaluate_dispute: 'Evaluación de la disputa',
  create_dispute_case: 'Registro de la disputa',
  list_transactions: 'Consulta de transacciones',
  get_transaction: 'Consulta de una transacción',
  list_dispute_cases: 'Consulta de casos',
  get_case: 'Consulta de un caso',
}

/** How an action ended. A result that is a policy reason code reads through `REASON_CODE_LABELS`. */
export const ACTION_RESULT_LABELS: Record<string, string> = {
  reached: 'Alcanzado',
  unavailable: 'No disponible',
  unverified: 'Sin verificar',
  refused: 'Rechazada',
  tool_failure: 'Falla de herramienta',
  confirmation_required: 'Requiere confirmación',
  confirmation_mismatch: 'La confirmación no coincide',
  idempotency_conflict: 'Solicitud repetida con datos distintos',
  duplicate_open_case: 'Ya existe un caso abierto',
  session_cap_reached: 'Límite de la sesión alcanzado',
  decision_missing: 'Falta la decisión de la política',
}

/** An unlisted machine value, said in Spanish and followed by its plain words ("some_new_state" →
 * "Sin etiqueta (some new state)"), so a value added on the backend reaches the agent neither as an
 * identifier nor as a sentence in another language, and still says what it was. */
export function humanize(value: string): string {
  const words = value.replaceAll('_', ' ').trim()
  return words === '' ? '—' : `Sin etiqueta (${words})`
}

/**
 * Looks a value up in a label table, falling back to `humanize`. `Object.hasOwn` keeps inherited
 * keys such as `constructor` from being mistaken for labels.
 */
function labelOf(labels: Readonly<Record<string, string>>, value: string): string {
  return Object.hasOwn(labels, value) ? (labels[value] ?? humanize(value)) : humanize(value)
}

/** The phase a conversation was in, or the unlabelled form for one this console does not know. */
export function phaseLabel(phase: string): string {
  return labelOf(PHASE_LABELS, phase)
}

/** The step the assistant took, in words. */
export function actionLabel(action: string): string {
  return labelOf(ACTION_LABELS, action)
}

/** How the step ended: a known outcome, a policy reason code, or the unlabelled form for anything else. */
export function actionResultLabel(result: string): string {
  if (Object.hasOwn(ACTION_RESULT_LABELS, result)) return labelOf(ACTION_RESULT_LABELS, result)
  return Object.hasOwn(REASON_CODE_LABELS, result)
    ? labelOf(REASON_CODE_LABELS, result)
    : humanize(result)
}
