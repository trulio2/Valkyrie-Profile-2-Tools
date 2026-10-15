import type { CheatCatalog, ICheatsRepository, ToolPageData } from '@/types'

export default class CheatsRepository implements ICheatsRepository {
  private base = `${import.meta.env.BASE_URL}cheats/`

  public getCheatsData(): ToolPageData {
    return {
      title: 'Cheats Patcher',
      intro:
        'Writes the cheats you choose into a copy of the game, so they work without an emulator cheat file. It runs the same patcher as the desktop tool, in your browser; your disc image is never uploaded.',
      bullets: [
        {
          text: 'Works on the USA release, undub, or a translated ISO.'
        },
        {
          text: 'A few cheats take under a minute; every cheat together takes about three.'
        }
      ],
      docs: []
    }
  }

  public async getCatalog(): Promise<CheatCatalog | null> {
    const response = await fetch(`${this.base}cheats.json`, {
      cache: 'no-store'
    })
    if (response.status === 404) return null
    if (!response.ok)
      throw new Error(`Could not load the cheat list (${response.status}).`)
    return response.json()
  }

  public bundleUrl(): string {
    return `${this.base}python.zip`
  }
}
