/**
 * Test helper: finds a message in the visible conversation list.
 */
import { screen, waitFor, within } from '@testing-library/react'

/** Waits for a message to appear in the visible conversation list, ignoring the spoken announcement. */
export async function findMessage(
  text: string | RegExp,
  listName = 'Messages',
): Promise<HTMLElement> {
  return waitFor(() => within(screen.getByRole('list', { name: listName })).getByText(text))
}
