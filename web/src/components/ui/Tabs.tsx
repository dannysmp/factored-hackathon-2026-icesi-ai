/** Thin wrappers over the Radix tabs primitive that attach this app's styling hooks. */
import * as RadixTabs from '@radix-ui/react-tabs'
import type { JSX } from 'react'
import './Tabs.css'

/**
 * The console's one component-library primitive: Radix's tested roving-tabindex and arrow-key
 * pattern for a queue split by language or trigger, which a hand-rolled `<button>` set would
 * otherwise have to reimplement. Radix is unstyled, so every visual value comes from the token
 * file (`src/styles/tokens.css`) through `Tabs.css`; no themed UI kit is pulled in.
 *
 * The four parts below pass every prop through to Radix, so its ARIA roles, `aria-selected`
 * state and keyboard behavior (arrow keys move between tabs and skip disabled ones) are
 * unchanged. Each part only adds a fixed class name, merged with any `className` the caller gives.
 */

/** The tab group root; owns which tab is selected (controlled or via `defaultValue`). */
export function Tabs(props: RadixTabs.TabsProps): JSX.Element {
  return <RadixTabs.Root {...props} />
}

/** The row of tab triggers. Give it an `aria-label` so the group has an accessible name. */
export function TabsList({ className, ...props }: RadixTabs.TabsListProps): JSX.Element {
  return <RadixTabs.List className={joinClassNames('tabs-list', className)} {...props} />
}

/** One tab button; `value` ties it to the `TabsContent` it reveals. */
export function TabsTrigger({ className, ...props }: RadixTabs.TabsTriggerProps): JSX.Element {
  return <RadixTabs.Trigger className={joinClassNames('tabs-trigger', className)} {...props} />
}

/** The panel shown while its `value` matches the selected tab. */
export function TabsContent({ className, ...props }: RadixTabs.TabsContentProps): JSX.Element {
  return <RadixTabs.Content className={joinClassNames('tabs-content', className)} {...props} />
}

/** Appends the caller's class name to the component's own, when there is one. */
function joinClassNames(base: string, extra: string | undefined): string {
  return extra === undefined ? base : `${base} ${extra}`
}
