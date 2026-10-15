import type { ToolPageData } from '@/types'

type ToolPageProps = {
  data: ToolPageData
  children?: React.ReactNode
}

export default function ToolPage({ data, children }: ToolPageProps) {
  const { title, intro, bullets, docs } = data

  return (
    <section className="tool-page">
      <h1>{title}</h1>
      {intro && <p className="tool-intro">{intro}</p>}

      {bullets.length > 0 && (
        <ul className="tool-bullets">
          {bullets.map((bullet, index) => (
            <li key={index}>{bullet.text}</li>
          ))}
        </ul>
      )}

      {children}

      {docs.length > 0 && (
        <div className="tool-docs">
          <h2>Reference</h2>
          <ul>
            {docs.map((doc) => (
              <li key={doc.href}>
                <a
                  href={doc.href}
                  target="_blank"
                  rel="noreferrer"
                  className="link"
                >
                  {doc.label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
