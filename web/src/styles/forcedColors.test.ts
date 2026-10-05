/** Unit test: the stylesheet declarations that keep a selected sign-in persona visible under
 * forced colors. jsdom applies no forced-colors mode or layout, so these pin the declarations: a
 * heavier border in the system highlight colour whose extra width is taken out of the padding so
 * the card does not change size, and an avatar filled in system colours. */
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

function property(rules: string, name: string): string {
  const escaped = name.replace(/[-]/g, '\\$&')
  return new RegExp(`(?:^|[;\\s])${escaped}\\s*:\\s*([^;]+);?`).exec(rules)?.[1]?.trim() ?? ''
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
    expect(property(card, 'border-width')).toBe(
      'calc(var(--border-width-thin) + var(--extra-border))',
    )
    expect(property(card, 'border-color')).toBe('Highlight')
  })

  it('takes the extra border width out of the padding so the card keeps its size', () => {
    const card = declarations(block, '.personaSelected')
    const basePadding = property(declarations(signInCss, '.persona'), 'padding')
    expect(basePadding).toBe('var(--space-3) var(--space-4)')
    expect(property(card, '--extra-border')).toBe(
      'calc(var(--border-width-emphasis) * 2 - var(--border-width-thin))',
    )
    expect(property(card, 'padding')).toBe(
      basePadding.replace(/var\(--space-\d\)/g, (space) => `calc(${space} - var(--extra-border))`),
    )
  })

  it('fills the selected persona avatar with the highlight colour', () => {
    const avatar = declarations(block, '.personaSelected .avatar')
    expect(property(avatar, 'forced-color-adjust')).toBe('none')
    expect(property(avatar, 'background')).toBe('Highlight')
    expect(property(avatar, 'color')).toBe('HighlightText')
  })
})
