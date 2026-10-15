import IoC from '@/modules/ioc'
import { SERVICES, type IAboutService } from '@/types'

export default function About() {
  const aboutService = IoC.getOrCreateInstance<IAboutService>(SERVICES.ABOUT)

  const { intro, stats, repos } = aboutService.getAboutData()

  return (
    <section className="tool-page">
      <h1>About the Project</h1>
      <p className="tool-intro">{intro}</p>

      <div className="about-stats">
        {stats.map((stat) => (
          <div key={stat.label} className="about-stat">
            <div className="about-stat-value">{stat.value}</div>
            <div className="about-stat-label">{stat.label}</div>
          </div>
        ))}
      </div>

      <div className="tool-docs">
        <h2>Repositories</h2>
        <ul>
          {repos.map((repo) => (
            <li key={repo.href}>
              <a
                href={repo.href}
                target="_blank"
                rel="noreferrer"
                className="link"
              >
                {repo.label}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
