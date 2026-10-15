import type { ToolPageData } from './tool.types'

export interface IGlyphsService {
  getGlyphsData(): ToolPageData
}

export interface IGlyphsRepository {
  getGlyphsData(): ToolPageData
}
