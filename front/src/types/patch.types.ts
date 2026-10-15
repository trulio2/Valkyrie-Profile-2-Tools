export type PatchEntry = {
  tool?: string
  language: string
  label: string
  version: string
  path: string
  download: number
  outputSize: number
}

export type PatchIndex = {
  format: string
  patches: PatchEntry[]
}

export type PatchOp = ['s' | 'd', number, number]

export type PatchPart = {
  file: string
  size: number
  sha256: string
  packedSize: number
  packedSha256: string
}

export type PatchOption = {
  id: string
  default: boolean
}

export type PatchVariant = {
  options: Record<string, boolean>
  target: { size: number; sha256: string }
  ops: PatchOp[]
}

export type PatchSource = {
  name: string
  size: number
  sha256: string
  dataSize: number
  parts: PatchPart[]
  variants: PatchVariant[]
}

export type PatchManifest = {
  format: string
  tool?: string
  language: string
  label: string
  version: string
  outputName: string
  options: PatchOption[]
  sources: PatchSource[]
}

export type Progress = (done: number, total: number) => void

export interface IPatchService {
  getPatches(): Promise<PatchEntry[]>
  getManifest(entry: PatchEntry): Promise<PatchManifest>
  getData(
    entry: PatchEntry,
    source: PatchSource,
    onProgress: Progress
  ): Promise<Uint8Array>
  hashFile(file: File, onProgress: Progress): Promise<string>
  writeTarget(
    file: File,
    data: Uint8Array,
    variant: PatchVariant,
    handle: FileSystemFileHandle | null,
    onProgress: Progress
  ): Promise<Blob | null>
}

export interface IPatchRepository {
  getPatchIndex(): Promise<PatchIndex>
  getManifest(path: string): Promise<PatchManifest>
  getBytes(
    path: string,
    onProgress: (received: number) => void
  ): Promise<Uint8Array>
  resolve(path: string): string
}
