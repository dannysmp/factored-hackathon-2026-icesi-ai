/** Unit test: machine values reach the agent as Spanish words, and an unknown one never as an identifier. */
import { describe, expect, it } from 'vitest'
import { HandoffTriggerSchema } from './contracts'
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
    expect(humanize('some_new_state')).toBe('Some new state')
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
    expect(phaseLabel('awaiting_review')).toBe('Awaiting review')
  })

  it('does not read an inherited property as a known stage', () => {
    expect(phaseLabel('constructor')).toBe('Constructor')
  })
})

describe('actionLabel', () => {
  it('names a step the assistant records, and falls back for a new one', () => {
    expect(actionLabel('evaluate_dispute')).toBe('Evaluación de la disputa')
    expect(actionLabel('list_dispute_cases')).toBe('Consulta de casos')
    expect(actionLabel('new_tool')).toBe('New tool')
  })
})

describe('actionResultLabel', () => {
  it('names how a step ended', () => {
    expect(actionResultLabel('confirmation_required')).toBe('Requiere confirmación')
  })

  it('reads a policy reason code through its own label', () => {
    expect(actionResultLabel('escalate_fraud_claim')).toBe('Escalado: reclamo de fraude')
  })

  it('falls back to plain words for an outcome it does not know', () => {
    expect(actionResultLabel('timed_out')).toBe('Timed out')
  })
})

describe('trigger labels', () => {
  it('has a label and a request sentence for every trigger the backend can send', () => {
    for (const trigger of HandoffTriggerSchema.options) {
      expect(TRIGGER_LABELS[trigger]).not.toBe('')
      expect(REQUEST_SUMMARY_LABELS[trigger]).not.toBe('')
    }
  })
})
