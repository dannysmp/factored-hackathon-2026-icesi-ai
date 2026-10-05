/**
 * Entry point of the customer chat: mounts `App` into the root element of `index.html` and loads
 * the global styles. It holds no logic, so it is excluded from the coverage gate in
 * `vite.config.ts`.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './index.css'

const root = document.getElementById('root')
if (root === null) {
  throw new Error('root element not found: index.html must have <div id="root">')
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
