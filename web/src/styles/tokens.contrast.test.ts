/** Unit test: every text and surface pair the design tokens define meets WCAG AA (AC-E10-17). */
import { describe, expect, it } from 'vitest'
import TOKENS_CSS from './tokens.css?raw'

type Scheme = 'light' | 'dark'

function token(name: string, scheme: Scheme): string {
  const match = new RegExp(
    `--${name}:\\s*light-dark\\(\\s*(#[0-9a-f]{6})\\s*,\\s*(#[0-9a-f]{6})\\s*\\)`,
  ).exec(TOKENS_CSS)
  if (match === null) {
    throw new Error(`token --${name} is not a light-dark() pair of hex colors`)
  }
  return (scheme === 'light' ? match[1] : match[2]) ?? ''
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => {
    const value = parseInt(hex.slice(start, start + 2), 16) / 255
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  const [r = 0, g = 0, b = 0] = channels
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return ((lighter ?? 0) + 0.05) / ((darker ?? 0) + 0.05)
}

// Text and the surface it is drawn on. Body text needs 4.5:1 (WCAG 2.1 AA, 1.4.3).
const TEXT_PAIRS: readonly (readonly [string, string])[] = [
  ['color-text', 'color-bg'],
  ['color-text', 'color-bg-subtle'],
  ['color-text-subtle', 'color-bg'],
  ['color-text-subtle', 'color-bg-subtle'],
  ['color-accent', 'color-bg'],
  ['color-accent-contrast', 'color-accent'],
  ['color-accent-contrast', 'color-accent-hover'],
  ['color-success', 'color-success-bg'],
  ['color-warning', 'color-warning-bg'],
  ['color-error', 'color-error-bg'],
  ['color-info', 'color-info-bg'],
]

describe('design token contrast', () => {
  it.each(['light', 'dark'] as const)('meets 4.5:1 for every text pair in %s mode', (scheme) => {
    for (const [foreground, background] of TEXT_PAIRS) {
      const ratio = contrast(token(foreground, scheme), token(background, scheme))
      expect(ratio, `${foreground} on ${background} (${scheme})`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('computes the contrast ratio correctly at its two extremes', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5)
    expect(contrast('#777777', '#777777')).toBeCloseTo(1, 5)
  })
})
