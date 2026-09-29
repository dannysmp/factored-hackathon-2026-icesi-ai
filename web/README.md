# Web frontend

React chat UI and human-agent console (ADR-10). The customer chat is replayed against a scripted
fixture conversation for now; a live endpoint, gated on the demonstration sign-in broker and the
conversation store, comes next.

## Stack

| Tool                                                                                | What it is used for                                                         |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| [Vite](https://vite.dev)                                                            | Dev server and production build                                             |
| React 19 + TypeScript (strict, `noUncheckedIndexedAccess`)                          | The UI                                                                      |
| [Vitest](https://vitest.dev) + [Testing Library](https://testing-library.com)       | Unit and component tests, with a coverage gate                              |
| [jest-axe](https://github.com/nickcolley/jest-axe)                                  | Automated accessibility checks (WCAG 2.2 AA target) in every component test |
| ESLint (`typescript-eslint`, `react-hooks`, `react-refresh`, `jsx-a11y`) + Prettier | Lint and format                                                             |

[Zod](https://zod.dev) validates every turn against the same shape as `contracts/service_v1/api.py`'s
`TurnRequest`/`TurnResponse`. No design system yet.

**Known gaps:**

- ESLint is pinned to `^9` rather than the current `^10`, because `eslint-plugin-jsx-a11y@6.10.2`
  does not yet declare `10` in its peer range. Move both together once a jsx-a11y release
  supports it.
- The `web` CI job has no dependency-vulnerability scan yet; add one once a real HTTP client adds
  supply-chain surface beyond `react`/`react-dom`/`zod`.

## Customer chat

`src/features/customer-chat/` is a self-contained feature (frontend standard, section 2):
`contracts.ts` (the Zod schemas), `client.ts` (the `ChatClient` seam and its only implementation,
`FixtureChatClient`, which replays `fixtures.ts`'s scripted English conversation), `useConversation.ts`
(the turn-by-turn state) and `components/` (the reference-date banner, the message list with a
polite live region, numbered choice buttons, the confirmation button, the text form). A live
`ChatClient` implementation, against the real turn endpoint, comes later; this one only defines
the seam it will fill.

## Commands

```bash
npm install        # installs from package-lock.json
npm run dev         # dev server
npm run build       # tsc -b && vite build
npm run lint        # eslint .
npm run format       # prettier --check .
npm run format:fix   # prettier --write .
npm run test         # vitest run --coverage
```

Node 22 (`.nvmrc`), matching the version CI installs.

## Accessibility

Every component test asserts `expect(await axe(container)).toHaveNoViolations()`
(`test/setup.ts` registers the matcher; `test/matchers.d.ts` types it for Vitest, since
`@types/jest-axe` only augments Jest's own matcher namespace). This catches a real class of
mistakes — `src/App.test.tsx`'s second test failed during development on a stale render leaking a
second `<main>` landmark into the document, exactly the kind of issue automated axe checks are for
— but it is a floor, not a ceiling: a keyboard-only pass is still done by hand on every new flow
(frontend standard, section 7).
