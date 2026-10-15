const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/'

type PyProxy = { toJs(options?: object): unknown; destroy(): void }
type Pyodide = {
  globals: { set(name: string, value: unknown): void }
  unpackArchive(
    buffer: ArrayBuffer,
    format: string,
    options?: { extractDir?: string }
  ): void
  runPythonAsync(code: string): Promise<PyProxy>
}
type PlanRequest = {
  id: number
  file: File
  selected: string[]
  corruptSave: boolean
  bundleUrl: string
}

declare const FileReaderSync: {
  new (): { readAsArrayBuffer(blob: Blob): ArrayBuffer }
}

const SETUP = [
  'import os, sys',
  "os.environ['VP2_OVERLAY_CACHE'] = '0'",
  "sys.path.insert(0, '/app')",
  'from tools.cheat_patcher import web'
].join('\n')

const PLAN = [
  'import json',
  'writes, names = web.plan(read_at, file_size, json.loads(selected_json), corrupt_save, report)',
  '[[[offset, data] for offset, data in writes], names]'
].join('\n')

let ready: Promise<Pyodide> | null = null

async function load(bundleUrl: string): Promise<Pyodide> {
  const { loadPyodide } = await import(
    /* @vite-ignore */ `${PYODIDE}pyodide.mjs`
  )
  const pyodide: Pyodide = await loadPyodide({ indexURL: PYODIDE })
  const response = await fetch(bundleUrl)
  if (!response.ok)
    throw new Error(`Could not load the cheat patcher (${response.status}).`)
  pyodide.unpackArchive(await response.arrayBuffer(), 'zip', {
    extractDir: '/app'
  })
  await pyodide.runPythonAsync(SETUP)
  return pyodide
}

onmessage = async ({ data: request }: MessageEvent<PlanRequest>) => {
  const { id, file } = request
  const say = (message: string) => postMessage({ id, message })
  try {
    if (!ready) {
      say('Loading Python in your browser…')
      ready = load(request.bundleUrl).catch((error) => {
        ready = null
        throw error
      })
    }
    const pyodide = await ready
    const reader = new FileReaderSync()
    pyodide.globals.set(
      'read_at',
      (offset: number, size: number) =>
        new Uint8Array(
          reader.readAsArrayBuffer(file.slice(offset, offset + size))
        )
    )
    pyodide.globals.set('report', say)
    pyodide.globals.set('file_size', file.size)
    pyodide.globals.set('selected_json', JSON.stringify(request.selected))
    pyodide.globals.set('corrupt_save', request.corruptSave)
    const result = await pyodide.runPythonAsync(PLAN)
    const [writes, patches] = result.toJs() as [
      [number, Uint8Array][],
      string[]
    ]
    result.destroy()
    postMessage({ id, writes, patches })
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error)
    postMessage({ id, error: text.split('\n').filter(Boolean).pop() ?? text })
  }
}
