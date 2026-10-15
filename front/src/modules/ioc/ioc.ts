import {
  AboutRepository,
  AboutService,
  ChangelogRepository,
  ChangelogService,
  CheatsRepository,
  CheatsService,
  GlyphsRepository,
  GlyphsService,
  HomeRepository,
  HomeService,
  PatchRepository,
  PatchService,
  TranslationRepository,
  TranslationService,
  VoicesRepository,
  VoicesService
} from '@/modules/services'
import {
  type IAboutRepository,
  type IChangelogRepository,
  type ICheatsRepository,
  type IGlyphsRepository,
  type IHomeRepository,
  type IPatchRepository,
  type ITranslationRepository,
  type IVoicesRepository,
  REPOSITORIES,
  SERVICES
} from '@/types'

class IoC {
  private instances: Record<string, unknown> = {}

  public getOrCreateInstance<T>(name: string): T {
    const instance = this.instances[name]

    if (instance) return instance as T

    let newInstance: unknown

    switch (name) {
      case SERVICES.ABOUT:
        newInstance = new AboutService(
          this.getOrCreateInstance<IAboutRepository>(REPOSITORIES.ABOUT)
        )
        break
      case SERVICES.CHANGELOG:
        newInstance = new ChangelogService(
          this.getOrCreateInstance<IChangelogRepository>(REPOSITORIES.CHANGELOG)
        )
        break
      case SERVICES.CHEATS:
        newInstance = new CheatsService(
          this.getOrCreateInstance<ICheatsRepository>(REPOSITORIES.CHEATS)
        )
        break
      case SERVICES.GLYPHS:
        newInstance = new GlyphsService(
          this.getOrCreateInstance<IGlyphsRepository>(REPOSITORIES.GLYPHS)
        )
        break
      case SERVICES.HOME:
        newInstance = new HomeService(
          this.getOrCreateInstance<IHomeRepository>(REPOSITORIES.HOME)
        )
        break
      case SERVICES.PATCH:
        newInstance = new PatchService(
          this.getOrCreateInstance<IPatchRepository>(REPOSITORIES.PATCH)
        )
        break
      case SERVICES.TRANSLATION:
        newInstance = new TranslationService(
          this.getOrCreateInstance<ITranslationRepository>(
            REPOSITORIES.TRANSLATION
          )
        )
        break
      case SERVICES.VOICES:
        newInstance = new VoicesService(
          this.getOrCreateInstance<IVoicesRepository>(REPOSITORIES.VOICES)
        )
        break

      case REPOSITORIES.ABOUT:
        newInstance = new AboutRepository()
        break
      case REPOSITORIES.CHANGELOG:
        newInstance = new ChangelogRepository()
        break
      case REPOSITORIES.CHEATS:
        newInstance = new CheatsRepository()
        break
      case REPOSITORIES.GLYPHS:
        newInstance = new GlyphsRepository()
        break
      case REPOSITORIES.HOME:
        newInstance = new HomeRepository()
        break
      case REPOSITORIES.PATCH:
        newInstance = new PatchRepository()
        break
      case REPOSITORIES.TRANSLATION:
        newInstance = new TranslationRepository()
        break
      case REPOSITORIES.VOICES:
        newInstance = new VoicesRepository()
        break
      default:
        break
    }

    this.instances[name] = newInstance

    return newInstance as T
  }

  public cleanUp(name: string): void {
    delete this.instances[name]
  }
}

export default new IoC()
