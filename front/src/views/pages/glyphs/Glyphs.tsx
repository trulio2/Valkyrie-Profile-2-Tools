import IoC from '@/modules/ioc'
import Patcher from '@/components/patcher/Patcher'
import ToolPage from '@/components/tool/ToolPage'
import { type IGlyphsService, SERVICES } from '@/types'

export default function Glyphs() {
  const glyphsService = IoC.getOrCreateInstance<IGlyphsService>(SERVICES.GLYPHS)

  return (
    <ToolPage data={glyphsService.getGlyphsData()}>
      <Patcher tool="glyphs" action="Create glyph texture ISO" />
    </ToolPage>
  )
}
