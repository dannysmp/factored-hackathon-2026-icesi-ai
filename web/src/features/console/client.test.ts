/** Unit tests: `FixtureQueueClient` replays and filters a queue snapshot. */
import { describe, expect, it } from 'vitest'
import { FixtureQueueClient } from './client'
import { DEMO_QUEUE, EMPTY_QUEUE } from './fixtures'

describe('FixtureQueueClient', () => {
  it('answers the whole queue when no filter is given', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({})

    expect(response.items).toHaveLength(DEMO_QUEUE.items.length)
    expect(response.reference_date).toBe(DEMO_QUEUE.reference_date)
  })

  it('filters by language', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({ language: 'pt' })

    expect(response.items.every((item) => item.language === 'pt')).toBe(true)
    expect(response.items.length).toBeGreaterThan(0)
  })

  it('filters by trigger', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({ trigger: 'fraud_report' })

    expect(response.items.every((item) => item.trigger === 'fraud_report')).toBe(true)
    expect(response.items.length).toBeGreaterThan(0)
  })

  it('composes both filters', async () => {
    const client = new FixtureQueueClient(DEMO_QUEUE)

    const response = await client.fetchQueue({ language: 'pt', trigger: 'fraud_report' })

    expect(response.items).toEqual([])
  })

  it('answers an empty queue with no items', async () => {
    const client = new FixtureQueueClient(EMPTY_QUEUE)

    const response = await client.fetchQueue({})

    expect(response.items).toEqual([])
  })

  it('rejects a snapshot that does not match the contract', () => {
    expect(() => new FixtureQueueClient({ items: [{ ticket_ref: 'x' }] })).toThrow()
  })
})
