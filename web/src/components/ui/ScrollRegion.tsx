import { useEffect, useRef, useState } from 'react'
import type { JSX, ReactNode } from 'react'

/**
 * A wrapper that lets wide content scroll sideways without scrolling the page. When the content
 * really overflows, the wrapper becomes a named, focusable region so a keyboard user can scroll it
 * with the arrow keys; when everything fits, it adds no tab stop and no landmark.
 */
export function ScrollRegion({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const [scrollable, setScrollable] = useState(false)

  useEffect(() => {
    const element = ref.current
    if (element === null) return undefined
    const measure = (): void => {
      setScrollable(element.scrollWidth > element.clientWidth)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => {
      observer.disconnect()
    }
  }, [])

  return (
    <div
      ref={ref}
      className={className}
      role={scrollable ? 'region' : undefined}
      aria-label={scrollable ? label : undefined}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a region that scrolls must be focusable so the keyboard can scroll it
      tabIndex={scrollable ? 0 : undefined}
    >
      {children}
    </div>
  )
}
