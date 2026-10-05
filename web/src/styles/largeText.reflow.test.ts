import { describe, expect, it } from 'vitest'
import turnFormCss from '../features/customer-chat/components/TurnForm.module.css?raw'
import resultCardCss from '../features/customer-chat/components/ResultCard.module.css?raw'
import signInCss from '../features/sign-in/SignInScreen.module.css?raw'
import queueCss from '../features/console/QueueScreen.css?raw'
import ticketDetailCss from '../features/console/TicketDetailScreen.css?raw'

/** The declarations of the first rule whose selector is exactly `selector`. */
function declarations(css: string, selector: string): string {
  const escaped = selector.replace(/[.[\]()]/g, '\\$&')
  const rule = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(
    css.replace(/\/\*[\s\S]*?\*\//g, ''),
  )
  return rule?.[1] ?? ''
}

/*
 * jsdom applies no layout, so these pin the declarations that let a layout reflow when the reader
 * enlarges the browser's text size: rows that wrap instead of pushing a control off the screen,
 * flex children that may shrink below their content, and long words that may break.
 */
describe('layout under enlarged text', () => {
  it('wraps the message bar so the send button drops below the field instead of leaving the screen', () => {
    expect(declarations(turnFormCss, '.form')).toMatch(/flex-wrap:\s*wrap/)
    expect(declarations(turnFormCss, '.field')).toMatch(/min-width:\s*0/)
    expect(declarations(turnFormCss, '.field')).toMatch(/flex:\s*1 1 12rem/)
    expect(declarations(turnFormCss, '.input')).toMatch(/min-width:\s*0/)
  })

  it('lets the result heading shrink and break beside its icon', () => {
    const title = declarations(resultCardCss, '.title')
    expect(title).toMatch(/min-width:\s*0/)
    expect(title).toMatch(/overflow-wrap:\s*anywhere/)
  })

  it('lets a persona card stack its text under the avatar and the access code row wrap', () => {
    expect(declarations(signInCss, '.persona')).toMatch(/flex-wrap:\s*wrap/)
    expect(declarations(signInCss, '.personaText')).toMatch(/flex:\s*1 1 8rem/)
    expect(declarations(signInCss, '.accessCodeRow')).toMatch(/flex-wrap:\s*wrap/)
    expect(declarations(signInCss, '.accessCode')).toMatch(/flex:\s*1 1 8rem/)
  })

  it('keeps the queue table’s hidden text inside its scroll region so the page does not scroll sideways', () => {
    expect(declarations(queueCss, '.queue-table-scroll')).toMatch(/position:\s*relative/)
  })

  it('lets a long case reference break rather than overflow a narrow screen', () => {
    const ref = declarations(ticketDetailCss, '.ticket-detail-screen .ticket-ref')
    expect(ref).toMatch(/overflow-wrap:\s*anywhere/)
    expect(ref).not.toMatch(/white-space:\s*nowrap/)
  })
})
