/** Component test: the shared button's variants, defaults, disabled state and accessibility. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'

describe('Button', () => {
  it('is a plain button by default, so inside a form it never submits by accident', async () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => {
      event.preventDefault()
    })
    const user = userEvent.setup()
    render(
      <form onSubmit={onSubmit}>
        <Button>Ayuda</Button>
      </form>,
    )

    expect(screen.getByRole('button', { name: 'Ayuda' })).toHaveAttribute('type', 'button')
    await user.click(screen.getByRole('button', { name: 'Ayuda' }))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('submits a form when it asks for type=submit', async () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => {
      event.preventDefault()
    })
    const user = userEvent.setup()
    render(
      <form onSubmit={onSubmit}>
        <Button type="submit">Enviar</Button>
      </form>,
    )

    await user.click(screen.getByRole('button', { name: 'Enviar' }))
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('calls its handler on a click and on the keyboard', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(<Button onClick={onClick}>Volver</Button>)

    await user.click(screen.getByRole('button', { name: 'Volver' }))
    expect(screen.getByRole('button', { name: 'Volver' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledTimes(2)
  })

  it('does nothing and is announced as disabled when disabled', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(
      <Button disabled onClick={onClick}>
        Enviar
      </Button>,
    )

    expect(screen.getByRole('button', { name: 'Enviar' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Enviar' }))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('merges the caller class name with its own variant and size classes', () => {
    render(
      <Button variant="primary" large fullWidth className="extra">
        Confirmar
      </Button>,
    )

    const button = screen.getByRole('button', { name: 'Confirmar' })
    expect(button).toHaveClass('extra')
    expect(button.className.split(' ').length).toBeGreaterThanOrEqual(5)
  })

  it.each(['primary', 'secondary', 'quiet'] as const)(
    'has no accessibility violations as %s, enabled or disabled',
    async (variant) => {
      const { container } = render(
        <div>
          <Button variant={variant}>Activo</Button>
          <Button variant={variant} disabled>
            Inactivo
          </Button>
        </div>,
      )

      expect(await axe(container)).toHaveNoViolations()
    },
  )
})
