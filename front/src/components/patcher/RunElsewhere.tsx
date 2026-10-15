import { Link } from 'react-router-dom'
import { runStore } from './run.store'

export default function RunElsewhere({ tool }: { tool: string }) {
  const running = runStore((state) => state.running)
  if (running === null || running === tool) return null
  return (
    <p className="patch-status">
      A patch is already running on the{' '}
      <Link to={`/${running}`}>
        {running[0].toUpperCase() + running.slice(1)}
      </Link>{' '}
      page. You can start this one when it finishes.
    </p>
  )
}
