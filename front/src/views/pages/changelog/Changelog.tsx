import IoC from '@/modules/ioc'
import { type IChangelogService, SERVICES, tagLabel } from '@/types'

function Changelog() {
  const changelogService = IoC.getOrCreateInstance<IChangelogService>(
    SERVICES.CHANGELOG
  )

  const entries = changelogService.getEntries()

  return (
    <section className="changelog">
      <h1>Changelog</h1>
      <p className="changelog-sub">Release notes for the translation patch.</p>

      <ol className="changelog-list">
        {entries.map((entry) => (
          <li key={entry.version} className="changelog-entry">
            <header className="changelog-header">
              <span className="changelog-version">v{entry.version}</span>
              <time className="changelog-date" dateTime={entry.date}>
                {entry.date}
              </time>
            </header>

            <ul className="changelog-items">
              {entry.items.map((item, index) => (
                <li key={index} className="changelog-item">
                  <span
                    className={`changelog-tag changelog-tag-${item.type}`}
                    aria-label={tagLabel[item.type]}
                  >
                    {tagLabel[item.type]}
                  </span>
                  <span className="changelog-text">{item.text}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  )
}

export default Changelog
