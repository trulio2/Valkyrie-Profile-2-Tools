import type {
  CheatCatalog,
  CheatPlan,
  ICheatsRepository,
  ICheatsService,
  PatchOp,
  ToolPageData
} from '@/types'

type WorkerReply = {
  id: number
  message?: string
  writes?: [number, Uint8Array][]
  patches?: string[]
  error?: string
}

export function writesToOps(writes: [number, Uint8Array][], size: number) {
  const ordered = [...writes].sort((left, right) => left[0] - right[0])
  const data = new Uint8Array(
    ordered.reduce((total, [, bytes]) => total + bytes.length, 0)
  )
  const ops: PatchOp[] = []
  let cursor = 0
  let stored = 0
  for (const [offset, bytes] of ordered) {
    if (offset < cursor || offset + bytes.length > size) {
      throw new Error('The cheat patcher returned overlapping writes.')
    }
    if (offset > cursor) ops.push(['s', cursor, offset - cursor])
    data.set(bytes, stored)
    ops.push(['d', stored, bytes.length])
    stored += bytes.length
    cursor = offset + bytes.length
  }
  if (cursor < size) ops.push(['s', cursor, size - cursor])
  return { ops, data }
}

export default class CheatsService implements ICheatsService {
  private worker: Worker | null = null
  private jobs = 0

  constructor(private cheatsRepository: ICheatsRepository) {}

  public getCheatsData(): ToolPageData {
    return this.cheatsRepository.getCheatsData()
  }

  public getCatalog(): Promise<CheatCatalog | null> {
    return this.cheatsRepository.getCatalog()
  }

  public plan(
    file: File,
    selected: string[],
    corruptSave: boolean,
    onMessage: (message: string) => void
  ): Promise<CheatPlan> {
    this.worker ??= new Worker(new URL('./cheats.worker.ts', import.meta.url), {
      type: 'module'
    })
    const worker = this.worker
    const id = ++this.jobs
    return new Promise((resolve, reject) => {
      const listener = ({ data: reply }: MessageEvent<WorkerReply>) => {
        if (reply.id !== id) return
        if (reply.message) {
          onMessage(reply.message)
          return
        }
        worker.removeEventListener('message', listener)
        if (reply.error) {
          reject(new Error(reply.error))
          return
        }
        try {
          const { ops, data } = writesToOps(reply.writes ?? [], file.size)
          resolve({ ops, data, patches: reply.patches ?? [] })
        } catch (error) {
          reject(error)
        }
      }
      worker.addEventListener('message', listener)
      worker.postMessage({
        id,
        file,
        selected,
        corruptSave,
        bundleUrl: this.cheatsRepository.bundleUrl()
      })
    })
  }
}
