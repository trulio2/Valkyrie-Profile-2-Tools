import type { ITranslationRepository, ToolPageData } from '@/types'

export default class TranslationRepository implements ITranslationRepository {
  public getTranslationData(): ToolPageData {
    return {
      title: 'Translation',
      intro:
        'Choose your disc image and this page builds the translated ISO on your computer. Nothing is installed, and your disc image is never uploaded.',
      bullets: [],
      docs: []
    }
  }
}
