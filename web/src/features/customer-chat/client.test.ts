/** Unit tests: `FixtureChatClient` replays its script in order and validates every turn. */
import { describe, expect, it } from 'vitest'
import type { ChatClient } from './client'
import { FixtureChatClient, ScriptExhaustedError } from './client'
import { FILE_DISPUTE_EN } from './fixtures'

describe('FixtureChatClient', () => {
  it('replays the script in order from start', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN)
    const first = await client.start()
    expect(first.turn_id).toBe('fixture-turn-0001')
    const second = await client.sendTurn('anything')
    expect(second.turn_id).toBe('fixture-turn-0002')
  })

  it('rejects once every scripted turn has been played', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN.slice(0, 1))
    await client.start()
    await expect(client.sendTurn('anything')).rejects.toThrow(ScriptExhaustedError)
  })

  it('restarts from the first turn when start is called again', async () => {
    const client: ChatClient = new FixtureChatClient(FILE_DISPUTE_EN)
    await client.start()
    await client.sendTurn('anything')
    const restarted = await client.start()
    expect(restarted.turn_id).toBe('fixture-turn-0001')
  })

  it('rejects a script whose turns do not match the real contract', () => {
    expect(() => new FixtureChatClient([{ reply: 'missing every other required field' }])).toThrow()
  })
})
