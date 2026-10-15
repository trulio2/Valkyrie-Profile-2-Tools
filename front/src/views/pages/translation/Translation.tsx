import IoC from '@/modules/ioc'
import Patcher from '@/components/patcher/Patcher'
import ToolPage from '@/components/tool/ToolPage'
import { type ITranslationService, SERVICES } from '@/types'

export default function Translation() {
  const translationService = IoC.getOrCreateInstance<ITranslationService>(
    SERVICES.TRANSLATION
  )

  return (
    <ToolPage data={translationService.getTranslationData()}>
      <Patcher tool="translation" action="Create translated ISO" />
    </ToolPage>
  )
}
