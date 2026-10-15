import type { PatchOp } from '@/types'
import {
  blobPatched,
  checkOps,
  hashBlob,
  streamPatched
} from './patch.apply.ts'

type HashRequest = { id: number; type: 'hash'; file: Blob }
type WriteRequest = {
  id: number
  type: 'write'
  file: File
  data: Uint8Array
  ops: PatchOp[]
  targetSize: number
  targetSha256: string
  handle: FileSystemFileHandle | null
}

const MISMATCH =
  'The finished ISO did not match the expected checksum, so it was not saved.'

async function writeToDisk(
  request: WriteRequest,
  handle: FileSystemFileHandle,
  onProgress: (done: number, total: number) => void
) {
  const writable = await handle.createWritable()
  try {
    const hash = await streamPatched(
      request.file,
      request.data,
      request.ops,
      (bytes) => writable.write(bytes as Uint8Array<ArrayBuffer>),
      onProgress
    )
    if (request.targetSha256 && hash !== request.targetSha256)
      throw new Error(MISMATCH)
  } catch (error) {
    await writable.abort()
    throw error
  }
  await writable.close()
}

onmessage = async ({
  data: request
}: MessageEvent<HashRequest | WriteRequest>) => {
  const onProgress = (done: number, total: number) =>
    postMessage({ id: request.id, progress: [done, total] })
  try {
    if (request.type === 'hash') {
      postMessage({
        id: request.id,
        hash: await hashBlob(request.file, onProgress)
      })
      return
    }
    checkOps(
      request.ops,
      request.file.size,
      request.data.length,
      request.targetSize
    )
    let blob: Blob | null = null
    if (request.handle) {
      await writeToDisk(request, request.handle, onProgress)
    } else {
      blob = blobPatched(request.file, request.data, request.ops)
      if (
        request.targetSha256 &&
        (await hashBlob(blob, onProgress)) !== request.targetSha256
      ) {
        throw new Error(MISMATCH)
      }
    }
    postMessage({ id: request.id, done: true, blob })
  } catch (error) {
    postMessage({
      id: request.id,
      error: error instanceof Error ? error.message : String(error)
    })
  }
}
