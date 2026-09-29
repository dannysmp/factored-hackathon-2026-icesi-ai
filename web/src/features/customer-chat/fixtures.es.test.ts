/** Proves the Spanish fixture is contract-valid and walks to a normal, successful end. */
import { describe, expect, it } from 'vitest'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_ES } from './fixtures.es'

describe('FILE_DISPUTE_ES', () => {
  it('is valid TurnResponse data that walks to a filed case and a farewell', async () => {
    const client = new FixtureChatClient(FILE_DISPUTE_ES)
    let turn = await client.start()
    expect(turn.lang).toBe('es')
    for (let i = 1; i < FILE_DISPUTE_ES.length; i += 1) {
      turn = await client.sendTurn()
    }
    expect(turn.end_session).toBe(true)
  })

  it('carries the case number on the filing-result turn, before the session ends', () => {
    const filingResult = FILE_DISPUTE_ES.find((turn) => turn.handoff_ticket !== null)
    expect(filingResult?.handoff_ticket).toBe('D-2001')
    expect(filingResult?.end_session).toBe(false)
  })
})
