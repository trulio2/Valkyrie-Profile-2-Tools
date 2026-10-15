import IoC from '@/modules/ioc'
import CheatPatcher from '@/components/patcher/CheatPatcher'
import ToolPage from '@/components/tool/ToolPage'
import { type ICheatsService, SERVICES } from '@/types'

export default function Cheats() {
  const cheatsService = IoC.getOrCreateInstance<ICheatsService>(SERVICES.CHEATS)

  return (
    <ToolPage data={cheatsService.getCheatsData()}>
      <CheatPatcher />
    </ToolPage>
  )
}
