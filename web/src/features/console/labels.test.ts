/** Unit test: machine values reach the agent as Spanish words, and an unknown one never as an identifier. */
import { describe, expect, it } from 'vitest'
import { HandoffTriggerSchema, type HandoffTrigger } from './contracts'
import {
  REQUEST_SUMMARY_LABELS,
  TRIGGER_LABELS,
  actionLabel,
  actionResultLabel,
  humanize,
  phaseLabel,
} from './labels'

describe('humanize', () => {
  it('writes an identifier as a sentence-case phrase', () => {
    expect(humanize('some_new_state')).toBe('Sin etiqueta (some new state)')
  })

  it('writes an empty value as an em dash', () => {
    expect(humanize('  ')).toBe('—')
  })
})

describe('phaseLabel', () => {
  it('names each stage a conversation reaches', () => {
    expect(phaseLabel('started')).toBe('Iniciada')
    expect(phaseLabel('clarifying')).toBe('En aclaración')
    expect(phaseLabel('confirming')).toBe('Esperando confirmación')
    expect(phaseLabel('closed')).toBe('Cerrada')
    expect(phaseLabel('handed_off')).toBe('Derivada a un agente')
    expect(phaseLabel('abandoned')).toBe('Abandonada')
  })

  it('falls back to plain words for a stage it does not know', () => {
    expect(phaseLabel('awaiting_review')).toBe('Sin etiqueta (awaiting review)')
  })

  it('does not read an inherited property as a known stage', () => {
    expect(phaseLabel('constructor')).toBe('Sin etiqueta (constructor)')
  })
})

describe('actionLabel', () => {
  it('names a step the assistant records, and falls back for a new one', () => {
    expect(actionLabel('evaluate_dispute')).toBe('Evaluación de la disputa')
    expect(actionLabel('list_dispute_cases')).toBe('Consulta de casos')
    expect(actionLabel('new_tool')).toBe('Sin etiqueta (new tool)')
  })
})

describe('actionResultLabel', () => {
  it('names how a step ended', () => {
    expect(actionResultLabel('confirmation_required')).toBe('Requiere confirmación')
  })

  it('words every outcome so it agrees with a masculine and a feminine action alike', () => {
    expect(`${actionLabel('turn_cap')}: ${actionResultLabel('reached')}`).toBe(
      'Límite de turnos: Se alcanzó',
    )
    expect(`${actionLabel('create_dispute_case')}: ${actionResultLabel('refused')}`).toBe(
      'Registro de la disputa: Se rechazó',
    )
  })

  it('reads a policy reason code through its own label', () => {
    expect(actionResultLabel('escalate_fraud_claim')).toBe('Escalado: reclamo de fraude')
  })

  it('falls back to plain words for an outcome it does not know', () => {
    expect(actionResultLabel('timed_out')).toBe('Sin etiqueta (timed out)')
  })
})

const EXPECTED_REQUEST_SUMMARIES: Record<HandoffTrigger, string> = {
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

describe('trigger labels', () => {
  it('has a label for every trigger the backend can send', () => {
    for (const trigger of HandoffTriggerSchema.options) {
      expect(TRIGGER_LABELS[trigger]).toBeTypeOf('string')
      expect(TRIGGER_LABELS[trigger].length).toBeGreaterThan(0)
    }
  })

  it('says each trigger as one fixed Spanish sentence', () => {
    expect(Object.keys(REQUEST_SUMMARY_LABELS).sort()).toEqual(
      [...HandoffTriggerSchema.options].sort(),
    )
    for (const trigger of HandoffTriggerSchema.options) {
      expect(REQUEST_SUMMARY_LABELS[trigger]).toBe(EXPECTED_REQUEST_SUMMARIES[trigger])
    }
  })
})
