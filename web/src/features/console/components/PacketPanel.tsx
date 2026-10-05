/** The handoff packet panel: the summary, verified facts, actions, evidence and open questions. */
import { useId } from 'react'
import type { JSX } from 'react'
import { ScrollRegion } from '../../../components/ui/ScrollRegion'
import type { HandoffPacket, LocalizedTitle } from '../contracts'
import type { Lang } from '../../customer-chat/contracts'
import {
  formatDate,
  formatDateTime,
  formatMoney,
  formatScoreAgainstThreshold,
  formatShare,
} from '../format'
import {
  CATEGORY_LABELS,
  LANGUAGE_LABELS,
  REASON_CODE_LABELS,
  REQUEST_SUMMARY_LABELS,
  SLOT_LABELS,
  TRANSACTION_STATUS_LABELS,
  actionLabel,
  actionResultLabel,
} from '../labels'

/** The title of a policy source in the case's own language — the customer's own words, never
 * the console's fixed Spanish (the fixed-Spanish rule covers console chrome, not case content).
 * Falls back to the first title on record only if the exact language is missing, which the
 * backend's `SourceRef` validator (one title per language) should never produce. */
function titleInLanguage(titles: readonly LocalizedTitle[], language: Lang): string {
  return titles.find((title) => title.lang === language)?.text ?? titles[0]?.text ?? '—'
}

/** What the system calls a customer it has no name for. */
const UNKNOWN_FIRST_NAME = 'Customer'

/** The customer's first name, or the Spanish word for "customer" when the system had no name. */
function customerName(firstName: string): string {
  return firstName === UNKNOWN_FIRST_NAME ? 'Cliente' : firstName
}

/**
 * The packet: the request first, as a summary of who is asking and why (with the case already
 * filed for it, when there is one, and the moment of the handoff), then the verified facts, the actions taken or refused, the evidence (reason codes, policy version, source
 * sections, and the risk score with its uncertainty, base rate and routing threshold when there is
 * one), and the open questions. Every value is written for an agent reading Spanish: a known
 * machine value through its label, dates and amounts in the console's own format, a score as a
 * percentage. The risk score always renders with a disclosure that it is a synthetic-data
 * estimate, not a real fraud signal — wherever the score appears, the caveat appears with it.
 *
 * Text in the customer's own language (the policy titles they were shown) carries that language
 * and a visible cue, so a Spanish reader knows it is not the console speaking.
 *
 * `TransactionFact`/`ProductLabel` carry no document number or full card/account number at all
 * (`contracts/service_v1/envelope.py`) — nothing here can expose one; the customer shows as a
 * first name and a masked id only.
 */
export function PacketPanel({ packet }: { packet: HandoffPacket }): JSX.Element {
  const summaryId = useId()
  const customerLanguage = LANGUAGE_LABELS[packet.language]
  const { risk } = packet.evidence
  const riskShares = risk === null ? null : formatScoreAgainstThreshold(risk.score, risk.threshold)

  return (
    <div className="packet">
      <section className="packet-summary" aria-labelledby={summaryId}>
        <h3 id={summaryId}>Solicitud</h3>
        <p className="packet-request">{REQUEST_SUMMARY_LABELS[packet.trigger]}</p>
        <dl>
          <dt>Cliente</dt>
          <dd>
            {customerName(packet.customer.first_name)} · {packet.customer.masked_id}
          </dd>
          <dt>Idioma del cliente</dt>
          <dd>{customerLanguage}</dd>
          {packet.category !== null && (
            <>
              <dt>Categoría</dt>
              <dd>{CATEGORY_LABELS[packet.category]}</dd>
            </>
          )}
          {packet.existing_case_number !== null && (
            <>
              <dt>Caso ya registrado</dt>
              <dd>
                <span className="ticket-ref">{packet.existing_case_number}</span>
              </dd>
            </>
          )}
          <dt>Fecha de referencia</dt>
          <dd>{formatDate(packet.reference_date)}</dd>
          <dt>Registrado</dt>
          <dd>{formatDateTime(packet.created_at)}</dd>
        </dl>
      </section>

      <h3>Transacciones verificadas</h3>
      {packet.verified_facts.length === 0 ? (
        <p>Ninguna transacción verificada.</p>
      ) : (
        <ScrollRegion className="queue-table-scroll" label="Transacciones verificadas">
          <table className="stacked-table">
            <caption className="sr-only">Transacciones verificadas</caption>
            <thead>
              <tr>
                <th scope="col">Referencia</th>
                <th scope="col">Fecha</th>
                <th scope="col">Comercio</th>
                <th scope="col">Monto</th>
                <th scope="col">Producto</th>
                <th scope="col">Estado</th>
              </tr>
            </thead>
            <tbody>
              {packet.verified_facts.map((fact) => (
                <tr key={fact.ref}>
                  <th scope="row">{fact.ref}</th>
                  <td data-label="Fecha">{formatDate(fact.occurred_on)}</td>
                  <td data-label="Comercio">{fact.merchant ?? '—'}</td>
                  <td data-label="Monto">
                    {fact.amount === null
                      ? '—'
                      : formatMoney(fact.amount.amount, fact.amount.currency)}
                  </td>
                  <td data-label="Producto">
                    {fact.product.name} ····{fact.product.last4}
                  </td>
                  <td data-label="Estado">{TRANSACTION_STATUS_LABELS[fact.status]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollRegion>
      )}

      <h3>Acciones</h3>
      {packet.actions.length === 0 && packet.attempted_action === null ? (
        <p>Ninguna acción tomada.</p>
      ) : (
        <ul>
          {packet.actions.map((action, index) => (
            // Actions carry no identifier of their own; position in the ordered list is stable.
            <li key={`${action.action}-${String(index)}`}>
              {actionLabel(action.action)}: {actionResultLabel(action.result)}
            </li>
          ))}
          {packet.attempted_action !== null && (
            <li>
              {actionLabel(packet.attempted_action.action)} — intento no completado:{' '}
              {actionResultLabel(packet.attempted_action.result)}
            </li>
          )}
        </ul>
      )}

      <h3>Evidencia</h3>
      <dl>
        <dt>Versión de la política</dt>
        <dd>{packet.evidence.policy_version}</dd>
        <dt>Motivos de la decisión</dt>
        <dd>
          {packet.evidence.reason_codes.length === 0 ? (
            '—'
          ) : (
            <ul className="packet-plain-list">
              {packet.evidence.reason_codes.map((code, index) => (
                <li key={`${code}-${String(index)}`}>{REASON_CODE_LABELS[code]}</li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
      {packet.evidence.sources.length > 0 && (
        <>
          <h4>Fuentes citadas</h4>
          <p className="packet-cue">Texto en el idioma del cliente ({customerLanguage}).</p>
          <ul>
            {packet.evidence.sources.map((source) => (
              <li key={source.section_id} lang={packet.language}>
                {titleInLanguage(source.titles, packet.language)}
              </li>
            ))}
          </ul>
        </>
      )}
      {risk !== null && riskShares !== null && (
        <>
          <h4>Puntaje de riesgo</h4>
          <dl>
            <dt>Puntaje</dt>
            <dd>{riskShares.score}</dd>
            <dt>Intervalo</dt>
            <dd>
              {formatShare(risk.interval_low)} – {formatShare(risk.interval_high)}
            </dd>
            <dt>Tasa base</dt>
            <dd>{formatShare(risk.base_rate)}</dd>
            <dt>Umbral de escalamiento</dt>
            <dd>{riskShares.threshold}</dd>
          </dl>
          <p className="packet-cue">
            Este puntaje es una estimación calculada con datos sintéticos, no con datos reales de
            fraude.
          </p>
        </>
      )}

      <h3>Preguntas abiertas</h3>
      {packet.open_questions.length === 0 ? (
        <p>Ninguna pregunta abierta.</p>
      ) : (
        <ul>
          {packet.open_questions.map((question) => (
            <li key={question.slot}>
              {SLOT_LABELS[question.slot]} ({question.attempts}{' '}
              {question.attempts === 1 ? 'intento' : 'intentos'})
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
