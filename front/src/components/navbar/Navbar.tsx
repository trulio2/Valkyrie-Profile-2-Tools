import { NavLink, Link } from 'react-router-dom'
import { runStore } from '@/components/patcher/run.store'

const links = [
  { to: '/', label: 'Home', end: true },
  { to: '/translation', label: 'Translation' },
  { to: '/glyphs', label: 'Glyphs' },
  { to: '/cheats', label: 'Cheats' },
  { to: '/voices', label: 'Voices' },
  { to: '/about', label: 'About' },
  { to: '/changelog', label: 'Changelog' }
]

function RunMark({ tool }: { tool: string }) {
  const running = runStore((state) => state.running)
  const progress = runStore((state) => state.panels[tool]?.progress) as
    | number
    | null
    | undefined
  if (running !== tool) return null
  return (
    <span className="navbar-run" title="A patch is running on this page">
      {typeof progress === 'number' && `${Math.floor(progress * 100)}%`}
    </span>
  )
}

export default function Navbar() {
  return (
    <nav className="navbar" aria-label="Primary">
      <Link to="/" className="navbar-brand">
        VP2 Tools
      </Link>
      <ul className="navbar-links">
        {links.map((link) => (
          <li key={link.to}>
            <NavLink
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                `navbar-link${isActive ? ' active' : ''}`
              }
            >
              {link.label}
              <RunMark tool={link.to.slice(1)} />
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
