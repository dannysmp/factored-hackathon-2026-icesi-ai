/** Test helpers for finding the persona cards on the sign-in screen by persona slug. */
import { screen, waitFor } from '@testing-library/react'

/** The persona card's radio for `slug`; throws at once when the screen does not show it. */
export function getPersonaRadio(slug: string): HTMLElement {
  const radio = screen
    .getAllByRole('radio')
    .find((candidate) => candidate.getAttribute('value') === slug)
  if (radio === undefined) throw new Error(`No persona card for "${slug}".`)
  return radio
}

/** The persona card's radio for `slug`, once the persona directory has loaded and shows it. */
export async function findPersonaRadio(slug: string): Promise<HTMLElement> {
  return waitFor(() => getPersonaRadio(slug))
}
