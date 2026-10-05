/**
 * Entry point of the agent console: mounts `ConsoleApp` into the root element of `console.html`
 * and loads the global styles. It holds no logic, so it is excluded from the coverage gate in
 * `vite.config.ts`.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConsoleApp } from './ConsoleApp'
import './index.css'

const root = document.getElementById('root')
if (root === null) {
  throw new Error('root element not found: console.html must have <div id="root">')
}

createRoot(root).render(
  <StrictMode>
    <ConsoleApp />
  </StrictMode>,
)
