import type { IGlyphsRepository, ToolPageData } from '@/types'

export default class GlyphsRepository implements IGlyphsRepository {
  public getGlyphsData(): ToolPageData {
    return {
      title: 'Glyph Textures',
      intro:
        'Makes a copy of the game that draws each letter as its own texture, so PCSX2 can replace the text with high-resolution textures. The anti-cheat is turned off in the same copy. Translated ISOs from the Translation page can include this already.',
      bullets: [
        {
          text: 'Exporting glyphs as PNG files and turning edited glyphs into textures still needs the desktop tool.'
        }
      ],
      docs: []
    }
  }
}
