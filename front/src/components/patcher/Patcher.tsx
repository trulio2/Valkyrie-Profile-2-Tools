import { useEffect, useState } from 'react'
import DiscDrop from './DiscDrop'
import RunElsewhere from './RunElsewhere'
import {
  chooseSaveHandle,
  gigabytes,
  megabytes,
  saveBlob,
  type Status
} from './disc'
import { claimRun, releaseRun, runStore, usePanelState } from './run.store'
import IoC from '@/modules/ioc'
import {
  type IPatchService,
  type PatchEntry,
  type PatchManifest,
  type PatchSource,
  SERVICES
} from '@/types'

const OPTION_TEXT: Record<string, { label: string; note: string }> = {
  'glyph-draw': {
    label: 'Glyph textures',
    note: 'Draws each letter as its own texture, so PCSX2 can replace the text with high-resolution textures.'
  },
  'corrupt-save': {
    label: 'Corrupt-save',
    note: 'Off for an OPL build; on keeps the corrupted-save and memory-card recovery words.'
  }
}

function sizes(sources: PatchSource[]): string {
  return sources
    .map((source) => `${source.name} (${source.size.toLocaleString()} bytes)`)
    .join(', ')
}

function preferredPatch(patches: PatchEntry[]): number {
  const wanted = navigator.languages.map((language) => language.toLowerCase())
  for (const language of wanted) {
    const exact = patches.findIndex(
      (patch) => patch.language.toLowerCase() === language
    )
    if (exact >= 0) return exact
  }
  for (const language of wanted) {
    const base = language.split('-')[0]
    const near = patches.findIndex(
      (patch) => patch.language.toLowerCase().split('-')[0] === base
    )
    if (near >= 0) return near
  }
  return 0
}

type PatcherProps = {
  tool: string
  action: string
}

export default function Patcher({ tool, action }: PatcherProps) {
  const patchService = IoC.getOrCreateInstance<IPatchService>(SERVICES.PATCH)

  const [patches, setPatches] = useState<PatchEntry[] | null>(null)
  const [selected, setSelected] = usePanelState<number | null>(
    tool,
    'selected',
    null
  )
  const [loaded, setLoaded] = useState<{
    path: string
    manifest: PatchManifest
  } | null>(null)
  const [file, setFile] = usePanelState<File | null>(tool, 'file', null)
  const [picked, setPicked] = usePanelState<Record<string, boolean>>(
    tool,
    'picked',
    {}
  )
  const [progress, setProgress] = usePanelState<number | null>(
    tool,
    'progress',
    null
  )
  const [status, setStatus] = usePanelState<Status | null>(tool, 'status', null)
  const running = runStore((state) => state.running)
  const busy = running === tool

  const current = selected ?? (patches ? preferredPatch(patches) : 0)
  const entry = patches?.[current]
  const manifest =
    loaded && loaded.path === entry?.path ? loaded.manifest : null
  const choices = Object.fromEntries(
    (manifest?.options ?? []).map((option) => [
      option.id,
      picked[option.id] ?? option.default
    ])
  )

  useEffect(() => {
    patchService
      .getPatches()
      .then((all) =>
        setPatches(
          all.filter((patch) => (patch.tool ?? 'translation') === tool)
        )
      )
      .catch((error: Error) => {
        setPatches([])
        setStatus({ text: error.message, kind: 'error' })
      })
  }, [patchService, tool, setStatus])

  useEffect(() => {
    if (!entry) return
    let current = true
    patchService
      .getManifest(entry)
      .then((manifest) => current && setLoaded({ path: entry.path, manifest }))
      .catch(
        (error: Error) =>
          current && setStatus({ text: error.message, kind: 'error' })
      )
    return () => {
      current = false
    }
  }, [patchService, entry, setStatus])

  function choose(chosen: File) {
    setFile(chosen)
    setStatus(null)
  }

  function track(label: string) {
    return (done: number, total: number) => {
      setProgress(total ? done / total : 0)
      setStatus({
        text: `${label} ${Math.floor((total ? done / total : 0) * 100)}%`
      })
    }
  }

  async function create() {
    if (!file || !manifest || !entry) return
    const unsupported = `This disc image is not one this patch supports. Supported: ${sizes(
      manifest.sources
    )}. An image with cheats or other changes can only be patched with the desktop tool.`
    const candidates = manifest.sources.filter(
      (source) => source.size === file.size
    )
    if (!candidates.length) {
      setStatus({ text: unsupported, kind: 'error' })
      return
    }
    const handle = await chooseSaveHandle(manifest.outputName)
    if (handle === undefined || !claimRun(tool)) return
    try {
      const hash = await patchService.hashFile(
        file,
        track('Step 1 of 3: checking your disc image…')
      )
      const source = candidates.find((candidate) => candidate.sha256 === hash)
      if (!source) throw new Error(unsupported)
      const variant = source.variants.find((candidate) =>
        Object.entries(choices).every(
          ([id, on]) => candidate.options[id] === on
        )
      )
      if (!variant) throw new Error('The patch has no build for these options.')
      const data = await patchService.getData(
        entry,
        source,
        track(`Step 2 of 3: downloading the patch for ${source.name}…`)
      )
      const blob = await patchService.writeTarget(
        file,
        data,
        variant,
        handle,
        track('Step 3 of 3: writing the ISO…')
      )
      if (blob) saveBlob(manifest.outputName, blob)
      setStatus({
        text: blob
          ? `Done. Your browser is saving ${manifest.outputName}.`
          : `Done. ${manifest.outputName} is saved and verified.`,
        kind: 'success'
      })
    } catch (error) {
      setStatus({
        text: error instanceof Error ? error.message : String(error),
        kind: 'error'
      })
    } finally {
      setProgress(null)
      releaseRun(tool)
    }
  }

  if (patches === null) {
    return <div className="spinner" role="status" aria-label="Loading"></div>
  }

  return (
    <div className="patcher">
      {patches.length === 0 && !status && (
        <p className="patch-status">No patch has been published yet.</p>
      )}

      {entry && (
        <>
          <dl className="patch-facts">
            {entry.language ? (
              <>
                <dt>Language</dt>
                <dd>
                  {patches.length > 1 ? (
                    <select
                      value={current}
                      disabled={busy}
                      onChange={(event) =>
                        setSelected(Number(event.target.value))
                      }
                    >
                      {patches.map((patch, index) => (
                        <option key={patch.path} value={index}>
                          {patch.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    entry.label
                  )}
                  {entry.version && ` · version ${entry.version}`}
                </dd>
              </>
            ) : (
              entry.version && (
                <>
                  <dt>Version</dt>
                  <dd>{entry.version}</dd>
                </>
              )
            )}
            <dt>Disc image</dt>
            <dd>
              Valkyrie Profile 2: Silmeria (SLUS-21452), one of:
              {manifest && (
                <ul className="patch-sources">
                  {manifest.sources.map((source) => (
                    <li key={source.sha256}>
                      {source.name}, {source.size.toLocaleString()} bytes
                    </li>
                  ))}
                </ul>
              )}
            </dd>
            <dt>Download</dt>
            <dd>up to {megabytes(entry.download)}</dd>
            <dt>Disk space</dt>
            <dd>{gigabytes(entry.outputSize)} for the new ISO</dd>
          </dl>

          <ul className="tool-bullets">
            <li>Use Chrome or Edge.</li>
            <li>
              Checking and writing a disc image takes a few minutes. Keep this
              browser tab open until it finishes; other pages of this site are
              fine.
            </li>
          </ul>

          <DiscDrop file={file} busy={busy} onChoose={choose} />

          {manifest && manifest.options.length > 0 && (
            <fieldset className="patch-options" disabled={busy}>
              {manifest.options.map((option) => (
                <label key={option.id} className="patch-option">
                  <input
                    type="checkbox"
                    checked={choices[option.id]}
                    onChange={(event) =>
                      setPicked({
                        ...picked,
                        [option.id]: event.target.checked
                      })
                    }
                  />
                  <span>
                    <strong>
                      {OPTION_TEXT[option.id]?.label ?? option.id}
                    </strong>
                    {OPTION_TEXT[option.id] && (
                      <small>{OPTION_TEXT[option.id].note}</small>
                    )}
                  </span>
                </label>
              ))}
            </fieldset>
          )}

          <p className="row">
            <button
              type="button"
              className="btn accent"
              disabled={!file || !manifest || running !== null}
              onClick={create}
            >
              {action}
            </button>
          </p>
          <RunElsewhere tool={tool} />
        </>
      )}

      {progress !== null && (
        <progress className="patch-progress" value={progress} />
      )}
      {status && (
        <p
          className={`patch-status${status.kind ? ` ${status.kind}` : ''}`}
          role="status"
        >
          {status.text}
        </p>
      )}
    </div>
  )
}
