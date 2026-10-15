import { createSHA256 } from 'hash-wasm'
import type { PatchOp } from '@/types'

export const STEP = 8 * 1024 * 1024

export type Progress = (done: number, total: number) => void

export async function hashBlob(
  blob: Blob,
  onProgress: Progress
): Promise<string> {
  const hasher = await createSHA256()
  hasher.init()
  for (let at = 0; at < blob.size; at += STEP) {
    hasher.update(new Uint8Array(await blob.slice(at, at + STEP).arrayBuffer()))
    onProgress(Math.min(at + STEP, blob.size), blob.size)
  }
  return hasher.digest('hex')
}

export function checkOps(
  ops: PatchOp[],
  sourceSize: number,
  dataSize: number,
  targetSize: number
) {
  let size = 0
  for (const [kind, at, length] of ops) {
    const limit = kind === 's' ? sourceSize : dataSize
    if (!(length > 0) || at < 0 || at + length > limit) {
      throw new Error('The patch is damaged.')
    }
    size += length
  }
  if (size !== targetSize) throw new Error('The patch is damaged.')
}

async function* pieces(source: Blob, data: Uint8Array, ops: PatchOp[]) {
  for (const [kind, at, length] of ops) {
    if (kind === 'd') {
      yield data.subarray(at, at + length)
      continue
    }
    for (let offset = 0; offset < length; offset += STEP) {
      const end = at + Math.min(offset + STEP, length)
      yield new Uint8Array(await source.slice(at + offset, end).arrayBuffer())
    }
  }
}

export async function streamPatched(
  source: Blob,
  data: Uint8Array,
  ops: PatchOp[],
  write: (bytes: Uint8Array) => Promise<void>,
  onProgress: Progress
): Promise<string> {
  const total = ops.reduce((sum, op) => sum + op[2], 0)
  const hasher = await createSHA256()
  hasher.init()
  const buffer = new Uint8Array(STEP)
  let fill = 0
  let done = 0
  const flush = async () => {
    if (!fill) return
    const view = buffer.subarray(0, fill)
    hasher.update(view)
    await write(view)
    fill = 0
  }
  for await (const piece of pieces(source, data, ops)) {
    let offset = 0
    while (offset < piece.length) {
      const count = Math.min(piece.length - offset, STEP - fill)
      buffer.set(piece.subarray(offset, offset + count), fill)
      fill += count
      offset += count
      if (fill === STEP) await flush()
    }
    done += piece.length
    onProgress(done, total)
  }
  await flush()
  return hasher.digest('hex')
}

export function blobPatched(
  source: Blob,
  data: Uint8Array,
  ops: PatchOp[]
): Blob {
  const parts: BlobPart[] = ops.map(([kind, at, length]) =>
    kind === 's' ? source.slice(at, at + length) : data.slice(at, at + length)
  )
  return new Blob(parts, { type: 'application/octet-stream' })
}
