import type { JSX } from 'react'

/**
 * The persistent reference-date line and the demonstration notice.
 *
 * The service renders both texts (api.py: "the client shows them and decides nothing"); this
 * component never computes or guesses either one.
 */
export function ReferenceBanner({
  referenceDateLine,
  demoNotice,
}: {
  referenceDateLine: string
  demoNotice: string | null
}): JSX.Element {
  return (
    <div role="note">
      <p>{referenceDateLine}</p>
      {demoNotice !== null && <p>{demoNotice}</p>}
    </div>
  )
}
