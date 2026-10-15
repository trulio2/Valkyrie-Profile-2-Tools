import type { IGlyphsRepository, IGlyphsService, ToolPageData } from '@/types'

export default class GlyphsService implements IGlyphsService {
  constructor(private glyphsRepository: IGlyphsRepository) {}

  public getGlyphsData(): ToolPageData {
    return this.glyphsRepository.getGlyphsData()
  }
}
