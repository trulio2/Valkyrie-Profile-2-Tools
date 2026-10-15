import type {
  IPatchRepository,
  IPatchService,
  PatchEntry,
  PatchManifest,
  PatchPart,
  PatchSource,
  PatchVariant,
  Progress
} from '@/types'

const FORMAT = 'vp2-web-patch-2'

type WorkerReply = {
  id: number
  progress?: [number, number]
  hash?: string
  done?: boolean
  blob?: Blob | null
  error?: string
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = new Uint8Array(
    await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>)
  )
  return Array.from(digest, (value) =>
    value.toString(16).padStart(2, '0')
  ).join('')
}

async function gunzip(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as Uint8Array<ArrayBuffer>])
    .stream()
    .pipeThrough(new DecompressionStream('gzip'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

async function unpack(bytes: Uint8Array, part: PatchPart): Promise<Uint8Array> {
  if (
    bytes.length === part.packedSize &&
    (await sha256(bytes)) === part.packedSha256
  ) {
    return gunzip(bytes)
  }
  if (bytes.length === part.size) return bytes
  throw new Error('A downloaded patch file is damaged. Try again.')
}

export default class PatchService implements IPatchService {
  private worker: Worker | null = null
  private jobs = 0

  constructor(private patchRepository: IPatchRepository) {}

  public async getPatches(): Promise<PatchEntry[]> {
    const index = await this.patchRepository.getPatchIndex()
    return index.format === FORMAT ? index.patches : []
  }

  public async getManifest(entry: PatchEntry): Promise<PatchManifest> {
    const manifest = await this.patchRepository.getManifest(entry.path)
    if (manifest.format !== FORMAT) {
      throw new Error(
        'This patch needs a newer version of this page. Reload it.'
      )
    }
    return manifest
  }

  public async getData(
    entry: PatchEntry,
    source: PatchSource,
    onProgress: Progress
  ): Promise<Uint8Array> {
    const folder = entry.path.slice(0, entry.path.lastIndexOf('/') + 1)
    const total = source.parts.reduce((sum, part) => sum + part.packedSize, 0)
    const data = new Uint8Array(source.dataSize)
    let received = 0
    let at = 0
    for (const part of source.parts) {
      const packed = await this.patchRepository.getBytes(
        folder + part.file,
        (count) => onProgress(received + count, total)
      )
      received += packed.length
      const raw = await unpack(packed, part)
      if (raw.length !== part.size || (await sha256(raw)) !== part.sha256) {
        throw new Error('A downloaded patch file is damaged. Try again.')
      }
      data.set(raw, at)
      at += raw.length
    }
    if (at !== source.dataSize) throw new Error('The patch is damaged.')
    return data
  }

  public hashFile(file: File, onProgress: Progress): Promise<string> {
    return this.run({ type: 'hash', file }, onProgress).then(
      (reply) => reply.hash!
    )
  }

  public writeTarget(
    file: File,
    data: Uint8Array,
    variant: PatchVariant,
    handle: FileSystemFileHandle | null,
    onProgress: Progress
  ): Promise<Blob | null> {
    return this.run(
      {
        type: 'write',
        file,
        data,
        ops: variant.ops,
        targetSize: variant.target.size,
        targetSha256: variant.target.sha256,
        handle
      },
      onProgress
    ).then((reply) => reply.blob ?? null)
  }

  private run(message: object, onProgress: Progress): Promise<WorkerReply> {
    this.worker ??= new Worker(new URL('./patch.worker.ts', import.meta.url), {
      type: 'module'
    })
    const worker = this.worker
    const id = ++this.jobs
    return new Promise((resolve, reject) => {
      const listener = ({ data: reply }: MessageEvent<WorkerReply>) => {
        if (reply.id !== id) return
        if (reply.progress) {
          onProgress(...reply.progress)
          return
        }
        worker.removeEventListener('message', listener)
        if (reply.error) reject(new Error(reply.error))
        else resolve(reply)
      }
      worker.addEventListener('message', listener)
      worker.postMessage({ id, ...message })
    })
  }
}
