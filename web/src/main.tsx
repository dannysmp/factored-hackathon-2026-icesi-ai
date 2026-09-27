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
