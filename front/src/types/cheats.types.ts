import type { PatchOp } from './patch.types'
import type { ToolPageData } from './tool.types'

export type CheatEntry = {
  name: string
  title: string
  summary: string
  requiresAntiCheat: boolean
}

export type CheatCatalog = {
  antiCheat: string
  cheats: CheatEntry[]
}

export type CheatPlan = {
  ops: PatchOp[]
  data: Uint8Array
  patches: string[]
}

export interface ICheatsService {
  getCheatsData(): ToolPageData
  getCatalog(): Promise<CheatCatalog | null>
  plan(
    file: File,
    selected: string[],
    corruptSave: boolean,
    onMessage: (message: string) => void
  ): Promise<CheatPlan>
}

export interface ICheatsRepository {
  getCheatsData(): ToolPageData
  getCatalog(): Promise<CheatCatalog | null>
  bundleUrl(): string
}
