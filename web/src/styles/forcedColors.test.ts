import { describe, expect, it } from 'vitest'
import signInCss from '../features/sign-in/SignInScreen.module.css?raw'

/** The body of the first `@media (forced-colors: active)` block, with its braces balanced. */
function forcedColorsBlock(css: string): string {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const start = source.indexOf('@media (forced-colors: active)')
  if (start === -1) return ''
  const open = source.indexOf('{', start)
  let depth = 0
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1
    if (source[index] === '}') depth -= 1
    if (depth === 0) return source.slice(open + 1, index)
  }
  return ''
}

function declarations(block: string, selector: string): string {
  const escaped = selector.replace(/[.[\]()]/g, '\\$&')
  return new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(block)?.[1] ?? ''
}

/*
 * Forced colors drop backgrounds, tints and shadows, and the persona radio is visually hidden, so a
 * selected persona card must carry its own mark in system colours or nothing shows the selection.
 */
describe('sign-in under forced colors', () => {
  const block = forcedColorsBlock(signInCss)

  it('draws the selected persona card with a heavier border in the highlight colour', () => {
    const card = declarations(block, '.personaSelected')
    expect(card).toMatch(/border-width:/)
    expect(card).toMatch(/border-color:\s*Highlight/)
  })

  it('fills the selected persona avatar with the highlight colour', () => {
    const avatar = declarations(block, '.personaSelected .avatar')
    expect(avatar).toMatch(/forced-color-adjust:\s*none/)
    expect(avatar).toMatch(/background:\s*Highlight/)
    expect(avatar).toMatch(/color:\s*HighlightText/)
  })
})
