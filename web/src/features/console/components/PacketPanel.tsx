import type { JSX } from 'react'
import type { HandoffPacket, LocalizedTitle } from '../contracts'
import type { Lang } from '../../customer-chat/contracts'
import {
  CATEGORY_LABELS,
  LANGUAGE_LABELS,
  REASON_CODE_LABELS,
  SLOT_LABELS,
  TRANSACTION_STATUS_LABELS,
} from '../labels'

/** The title of a policy source in the ticket's own language — the customer's own words, never
 * the console's fixed Spanish (D91 draws that line at console chrome, not case content). Falls
 * back to the first title on record only if the exact language is somehow missing, which the
 * backend's own `SourceRef` validator (one title per language) should never actually produce. */
function titleInLanguage(titles: readonly LocalizedTitle[], language: Lang): string {
  return titles.find((title) => title.lang === language)?.text ?? titles[0]?.text ?? '—'
}

function formatMoney(amount: string, currency: string): string {
  return `${amount} ${currency}`
}

/**
 * The packet (AC-E10-02): request, verified facts, actions taken or refused, evidence (reason
 * codes, policy version, source sections, and the risk score with its uncertainty, base rate and
 * routing threshold when there is one), open questions, the customer's language, and the data
 * reference date. The risk score always renders with a disclosure that it is a synthetic-data
 * estimate, not a real fraud signal — wherever the score appears, the caveat appears with it.
 *
 * `TransactionFact`/`ProductLabel` carry no document number or full card/account number at all
 * (`contracts/service_v1/envelope.py`) — nothing here can expose one (AC-E10-05).
 */
export function PacketPanel({ packet }: { packet: HandoffPacket }): JSX.Element {
  return (
    <div>
      <dl>
        <dt>Solicitud</dt>
        <dd>{packet.request_summary}</dd>
        <dt>Idioma del cliente</dt>
        <dd>{LANGUAGE_LABELS[packet.language]}</dd>
        <dt>Fecha de referencia</dt>
        <dd>{packet.reference_date}</dd>
        {packet.category !== null && (
          <>
            <dt>Categoría</dt>
            <dd>{CATEGORY_LABELS[packet.category]}</dd>
          </>
        )}
      </dl>

      <h3>Transacciones verificadas</h3>
      {packet.verified_facts.length === 0 ? (
        <p>Ninguna transacción verificada.</p>
      ) : (
        <div className="queue-table-scroll">
          <table>
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
                  <td>{fact.occurred_on}</td>
                  <td>{fact.merchant ?? '—'}</td>
                  <td>
                    {fact.amount === null
                      ? '—'
                      : formatMoney(fact.amount.amount, fact.amount.currency)}
                  </td>
                  <td>
                    {fact.product.name} ····{fact.product.last4}
                  </td>
                  <td>{TRANSACTION_STATUS_LABELS[fact.status]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Acciones</h3>
      {packet.actions.length === 0 && packet.attempted_action === null ? (
        <p>Ninguna acción tomada.</p>
      ) : (
        <ul>
          {packet.actions.map((action, index) => (
            // Actions carry no identifier of their own; position in the ordered list is stable.
            <li key={`${action.action}-${String(index)}`}>
              {action.action}: {action.result}
            </li>
          ))}
          {packet.attempted_action !== null && (
            <li>
              {packet.attempted_action.action} (intentada, no completada):{' '}
              {packet.attempted_action.result}
            </li>
          )}
        </ul>
      )}

      <h3>Evidencia</h3>
      <dl>
        <dt>Versión de política</dt>
        <dd>{packet.evidence.policy_version}</dd>
        <dt>Códigos de razón</dt>
        <dd>
          {packet.evidence.reason_codes.length === 0
            ? '—'
            : packet.evidence.reason_codes.map((code) => REASON_CODE_LABELS[code]).join(', ')}
        </dd>
      </dl>
      {packet.evidence.sources.length > 0 && (
        <>
          <h4>Fuentes citadas</h4>
          <ul>
            {packet.evidence.sources.map((source) => (
              <li key={source.section_id}>{titleInLanguage(source.titles, packet.language)}</li>
            ))}
          </ul>
        </>
      )}
      {packet.evidence.risk !== null && (
        <>
          <h4>Puntaje de riesgo</h4>
          <p>
            Este puntaje es una estimación calculada con datos sintéticos, no con datos reales de
            fraude.
          </p>
          <dl>
            <dt>Puntaje</dt>
            <dd>{packet.evidence.risk.score.toFixed(2)}</dd>
            <dt>Intervalo</dt>
            <dd>
              {packet.evidence.risk.interval_low.toFixed(2)} –{' '}
              {packet.evidence.risk.interval_high.toFixed(2)}
            </dd>
            <dt>Tasa base</dt>
            <dd>{packet.evidence.risk.base_rate.toFixed(2)}</dd>
            <dt>Umbral de escalamiento</dt>
            <dd>{packet.evidence.risk.threshold.toFixed(2)}</dd>
          </dl>
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
