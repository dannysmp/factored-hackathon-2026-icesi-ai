import * as RadixTabs from '@radix-ui/react-tabs'
import type { JSX } from 'react'
import './Tabs.css'

/**
 * The console's one vendored component-library primitive (ADR-19): Radix's tested
 * roving-tabindex and arrow-key pattern for a queue split by language or trigger, which a
 * hand-rolled `<button>` set would otherwise have to reimplement. Copied and owned here, not
 * pulled in as a themed runtime kit — every visual value comes from the token file
 * (`src/styles/tokens.css`), nothing from Radix's own (nonexistent) styling.
 */

export function Tabs(props: RadixTabs.TabsProps): JSX.Element {
  return <RadixTabs.Root {...props} />
}

export function TabsList({ className, ...props }: RadixTabs.TabsListProps): JSX.Element {
  return <RadixTabs.List className={joinClassNames('tabs-list', className)} {...props} />
}

export function TabsTrigger({ className, ...props }: RadixTabs.TabsTriggerProps): JSX.Element {
  return <RadixTabs.Trigger className={joinClassNames('tabs-trigger', className)} {...props} />
}

export function TabsContent({ className, ...props }: RadixTabs.TabsContentProps): JSX.Element {
  return <RadixTabs.Content className={joinClassNames('tabs-content', className)} {...props} />
}

function joinClassNames(base: string, extra: string | undefined): string {
  return extra === undefined ? base : `${base} ${extra}`
}
