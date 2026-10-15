import type { IPatchRepository, PatchIndex, PatchManifest } from '@/types'

export default class PatchRepository implements IPatchRepository {
  private base = `${import.meta.env.BASE_URL}patch/`

  public resolve(path: string): string {
    return this.base + path
  }

  public async getPatchIndex(): Promise<PatchIndex> {
    const response = await fetch(this.resolve('index.json'), {
      cache: 'no-store'
    })
    if (response.status === 404) return { format: '', patches: [] }
    if (!response.ok)
      throw new Error(`Could not load the patch list (${response.status}).`)
    return response.json()
  }

  public async getManifest(path: string): Promise<PatchManifest> {
    const response = await fetch(this.resolve(path), { cache: 'no-store' })
    if (!response.ok)
      throw new Error(`Could not load the patch (${response.status}).`)
    return response.json()
  }

  public async getBytes(
    path: string,
    onProgress: (received: number) => void
  ): Promise<Uint8Array> {
    const response = await fetch(this.resolve(path))
    if (!response.ok || !response.body) {
      throw new Error(`Could not download the patch (${response.status}).`)
    }
    const chunks: Uint8Array[] = []
    let received = 0
    const reader = response.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      received += value.length
      onProgress(received)
    }
    const bytes = new Uint8Array(received)
    let at = 0
    for (const chunk of chunks) {
      bytes.set(chunk, at)
      at += chunk.length
    }
    return bytes
  }
}
