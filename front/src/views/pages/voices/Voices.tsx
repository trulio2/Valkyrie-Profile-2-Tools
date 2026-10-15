import IoC from '@/modules/ioc'
import { type IVoicesService, SERVICES } from '@/types'

export default function Voices() {
  const voicesService = IoC.getOrCreateInstance<IVoicesService>(SERVICES.VOICES)

  const { title, intro, bullets, docs } = voicesService.getVoicesData()

  return (
    <section className="tool-page">
      <h1>{title}</h1>
      <p className="tool-intro">{intro}</p>

      <ul className="tool-bullets">
        {bullets.map((bullet, index) => (
          <li key={index}>{bullet.text}</li>
        ))}
      </ul>

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
    </section>
  )
}
