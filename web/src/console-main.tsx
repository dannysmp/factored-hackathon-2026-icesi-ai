/**
 * Bootstrap: mounts `ConsoleApp` into `console.html`'s root element. No logic worth a test lives
 * here; `vite.config.ts` excludes this file from the coverage gate for that reason.
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
