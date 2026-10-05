/** Component test: the sign-in card's language switcher, persona cards and access code control. */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import type { DemoPersonaSummary } from './contracts'
import { SignInScreen } from './SignInScreen'
import { en } from '../../i18n/en'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'
import { getPersonaRadio } from './personaRadios'

const CUSTOMERS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'carlos', display_name: 'Carlos', language: 'es', audience: 'customer' as const },
  { slug: 'joao', display_name: 'João', language: 'pt', audience: 'customer' as const },
  { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' as const },
]

afterEach(() => {
  vi.restoreAllMocks()
})

async function renderCustomerScreen(
  personas: readonly DemoPersonaSummary[] = CUSTOMERS,
): Promise<ReturnType<typeof userEvent.setup>> {
  vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(personas)
  const user = userEvent.setup()
  render(<SignInScreen onSignedIn={vi.fn()} />)
  await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })
  return user
}

describe('SignInScreen product heading', () => {
  it.each([
    ['ana', es],
    ['joao', pt],
    ['emma', en],
  ] as const)('names the product and describes it in the language of %s', async (slug, catalog) => {
    const user = await renderCustomerScreen()
    await user.click(getPersonaRadio(slug))

    expect(
      screen.getByRole('heading', { level: 2, name: catalog['signin.productName'] }),
    ).toBeInTheDocument()
    expect(screen.getByText(catalog['signin.productTagline'])).toBeInTheDocument()
  })

  it.each([
    ['ana', es],
    ['joao', pt],
  ] as const)(
    'shows no English title on the screen in the language of %s',
    async (slug, catalog) => {
      const user = await renderCustomerScreen()
      await user.click(getPersonaRadio(slug))

      expect(screen.queryByText(en['signin.productName'])).not.toBeInTheDocument()
      expect(screen.queryByText(/dispute intake/i)).not.toBeInTheDocument()
      expect(screen.getByText(catalog['signin.productName'])).toBeInTheDocument()
    },
  )
})

describe('SignInScreen language switcher', () => {
  it('offers the three languages and marks the one the screen speaks', async () => {
    await renderCustomerScreen()

    const switcher = screen.getByRole('group', { name: es['signin.languageSwitcherLabel'] })
    expect(within(switcher).getByRole('button', { name: 'Español' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(switcher).getByRole('button', { name: 'Português' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(within(switcher).getByRole('button', { name: 'English' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('selects the first persona who speaks the chosen language and speaks it', async () => {
    const user = await renderCustomerScreen()

    await user.click(screen.getByRole('button', { name: 'Português' }))

    expect(screen.getByRole('radio', { checked: true })).toHaveAttribute('value', 'joao')
    expect(screen.getByText(pt['signin.productTagline'])).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Português' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('keeps the selected persona when they already speak the chosen language', async () => {
    const user = await renderCustomerScreen()
    await user.click(getPersonaRadio('carlos'))

    await user.click(screen.getByRole('button', { name: 'Español' }))

    expect(screen.getByRole('radio', { checked: true })).toHaveAttribute('value', 'carlos')
  })

  it('follows a persona chosen directly', async () => {
    const user = await renderCustomerScreen()

    await user.click(getPersonaRadio('emma'))

    expect(screen.getByRole('button', { name: 'English' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Español' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('disables a language no persona speaks', async () => {
    await renderCustomerScreen(CUSTOMERS.filter((persona) => persona.language !== 'pt'))

    expect(screen.getByRole('button', { name: 'Português' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'English' })).toBeEnabled()
  })

  it('tells the page around it which language was chosen', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(CUSTOMERS)
    const onLanguageChange = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} onLanguageChange={onLanguageChange} />)
    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })

    await user.click(screen.getByRole('button', { name: 'English' }))

    expect(onLanguageChange).toHaveBeenLastCalledWith('en')
  })

  it('names each language in that language, so a reader can find their own', async () => {
    await renderCustomerScreen()

    expect(screen.getByRole('button', { name: 'Português' })).toHaveAttribute('lang', 'pt')
    expect(screen.getByRole('button', { name: 'English' })).toHaveAttribute('lang', 'en')
  })

  it('is not offered on the agent console, which stays in Spanish', async () => {
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue([
      {
        slug: 'agent-beatriz',
        display_name: 'Beatriz',
        language: 'pt',
        audience: 'agent' as const,
      },
    ])
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })
    expect(
      screen.queryByRole('group', { name: es['signin.languageSwitcherLabel'] }),
    ).not.toBeInTheDocument()
    expect(screen.getByText(es['signin.agentTagline'])).toBeInTheDocument()
  })
})

describe('SignInScreen persona cards', () => {
  it('shows the name, the language and the case of each persona in plain words', async () => {
    await renderCustomerScreen()

    const carlos = screen.getByRole('radio', { name: /^Carlos\s+Español/ })
    expect(carlos).toHaveAccessibleName(`Carlos Español ${es['signin.persona.carlos.case']}`)
    expect(screen.getAllByText(es['signin.persona.ana.case'])).toHaveLength(3)
  })

  it('shows initials on each card', async () => {
    await renderCustomerScreen()

    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('J')).toBeInTheDocument()
  })

  it('shows the case in the language of the screen', async () => {
    const user = await renderCustomerScreen()

    await user.click(getPersonaRadio('joao'))

    expect(screen.getByRole('radio', { name: /^João\s+Português/ })).toHaveAccessibleName(
      `João Português ${pt['signin.persona.joao.case']}`,
    )
    expect(screen.getByText(pt['signin.persona.carlos.case'])).toBeInTheDocument()
  })

  it('shows a persona with no known case by name alone', async () => {
    await renderCustomerScreen([
      { slug: 'zora', display_name: 'Zora', language: 'xx', audience: 'customer' as const },
    ])

    expect(screen.getByRole('radio', { name: 'Zora' })).toBeInTheDocument()
  })

  it('moves between cards with the arrow keys', async () => {
    const user = await renderCustomerScreen()
    getPersonaRadio('ana').focus()

    await user.keyboard('{ArrowDown}')

    expect(screen.getByRole('radio', { checked: true })).toHaveAttribute('value', 'carlos')
  })
})

describe('SignInScreen access code control', () => {
  it('hides the access code until asked to show it, and hides it again on request', async () => {
    const user = await renderCustomerScreen()
    const field = screen.getByLabelText(es['signin.accessCodeLabel'])
    await user.type(field, 'the-code')
    expect(field).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: /^Mostrar código de acceso$/i }))
    expect(field).toHaveAttribute('type', 'text')
    expect(field).toHaveValue('the-code')

    await user.click(screen.getByRole('button', { name: /^Ocultar código de acceso$/i }))
    expect(field).toHaveAttribute('type', 'password')
  })

  it('names the control in the language of the screen', async () => {
    const user = await renderCustomerScreen()
    await user.click(getPersonaRadio('emma'))

    expect(screen.getByRole('button', { name: 'Show access code' })).toHaveTextContent(
      en['signin.accessCodeShow'],
    )
  })

  it('keeps the show and hide control out of the sign-in submit', async () => {
    const onSignedIn = vi.fn()
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(CUSTOMERS)
    const signIn = vi.spyOn(api, 'signIn')
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)
    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })

    await user.click(screen.getByRole('button', { name: /^Mostrar/ }))

    expect(signIn).not.toHaveBeenCalled()
  })
})

describe('SignInScreen while the sign-in request is out', () => {
  it('locks the language switcher and the show and hide control', async () => {
    vi.spyOn(api, 'signIn').mockReturnValue(new Promise(() => undefined))
    const user = await renderCustomerScreen()
    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'a-code')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
    await screen.findByRole('button', { name: es['signin.submitting'] })

    const switcher = screen.getByRole('group', { name: es['signin.languageSwitcherLabel'] })
    for (const button of within(switcher).getAllByRole('button')) {
      expect(button).toBeDisabled()
    }
    expect(screen.getByRole('button', { name: /^Mostrar código de acceso$/i })).toBeDisabled()
  })
})

describe('SignInScreen accessibility', () => {
  it.each([
    ['ana', es],
    ['joao', pt],
    ['emma', en],
  ] as const)('has no detectable violations for %s', async (slug, catalog) => {
    const user = await renderCustomerScreen()
    await user.click(getPersonaRadio(slug))
    await user.type(screen.getByLabelText(catalog['signin.accessCodeLabel']), 'x')

    expect(await axe(document.body)).toHaveNoViolations()
  })
})
