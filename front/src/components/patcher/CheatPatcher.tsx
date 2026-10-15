import { useEffect, useState } from 'react'
import DiscDrop from './DiscDrop'
import RunElsewhere from './RunElsewhere'
import { chooseSaveHandle, saveBlob, type Status } from './disc'
import { claimRun, releaseRun, runStore, usePanelState } from './run.store'
import IoC from '@/modules/ioc'
import {
  type CheatCatalog,
  type ICheatsService,
  type IPatchService,
  SERVICES
} from '@/types'

const TOOL = 'cheats'

function outputName(file: File): string {
  return `${file.name.replace(/\.iso$/i, '')}-cheats.iso`
}

export default function CheatPatcher() {
  const cheatsService = IoC.getOrCreateInstance<ICheatsService>(SERVICES.CHEATS)
  const patchService = IoC.getOrCreateInstance<IPatchService>(SERVICES.PATCH)

  const [catalog, setCatalog] = useState<CheatCatalog | null | undefined>(
    undefined
  )
  const [picked, setPicked] = usePanelState<Set<string> | null>(
    TOOL,
    'picked',
    null
  )
  const [corruptSave, setCorruptSave] = usePanelState(
    TOOL,
    'corruptSave',
    false
  )
  const [file, setFile] = usePanelState<File | null>(TOOL, 'file', null)
  const [progress, setProgress] = usePanelState<number | null>(
    TOOL,
    'progress',
    null
  )
  const [status, setStatus] = usePanelState<Status | null>(TOOL, 'status', null)
  const running = runStore((state) => state.running)
  const busy = running === TOOL

  useEffect(() => {
    cheatsService
      .getCatalog()
      .then(setCatalog)
      .catch((error: Error) => {
        setCatalog(null)
        setStatus({ text: error.message, kind: 'error' })
      })
  }, [cheatsService, setStatus])

  if (catalog === undefined) {
    return <div className="spinner" role="status" aria-label="Loading"></div>
  }
  if (catalog === null) {
    return status ? (
      <p className="patch-status error">{status.text}</p>
    ) : (
      <p className="patch-status">
        The cheat patcher has not been published yet.
      </p>
    )
  }

  const antiCheat = catalog.antiCheat
  const chosen = picked ?? new Set([antiCheat])
  const needsAntiCheat = catalog.cheats.find(
    (cheat) => cheat.requiresAntiCheat && chosen.has(cheat.name)
  )
  const selected = new Set(chosen)
  if (needsAntiCheat) selected.add(antiCheat)
  const anyCheat = selected.size > 0

  function toggle(name: string, on: boolean) {
    const next = new Set(chosen)
    if (on) next.add(name)
    else next.delete(name)
    setPicked(next)
  }

  function toggleAll() {
    setPicked(
      anyCheat ? new Set() : new Set(catalog!.cheats.map((cheat) => cheat.name))
    )
  }

  async function create() {
    if (!file || !selected.size) return
    const name = outputName(file)
    const handle = await chooseSaveHandle(name)
    if (handle === undefined || !claimRun(TOOL)) return
    setProgress(null)
    try {
      const plan = await cheatsService.plan(
        file,
        catalog!.cheats
          .map((cheat) => cheat.name)
          .filter((cheat) => selected.has(cheat)),
        selected.has(antiCheat) && corruptSave,
        (message) =>
          setStatus({
            text: message.replace(
              /^patch: reading and rebuilding /,
              'Applying '
            )
          })
      )
      const blob = await patchService.writeTarget(
        file,
        plan.data,
        { options: {}, target: { size: file.size, sha256: '' }, ops: plan.ops },
        handle,
        (done, total) => {
          setProgress(total ? done / total : 0)
          setStatus({
            text: `Writing the ISO… ${Math.floor((total ? done / total : 0) * 100)}%`
          })
        }
      )
      if (blob) saveBlob(name, blob)
      setStatus({
        text: `Done: ${plan.patches.length} cheat(s) applied. ${
          blob ? `Your browser is saving ${name}.` : `${name} is saved.`
        }`,
        kind: 'success'
      })
    } catch (error) {
      setStatus({
        text: error instanceof Error ? error.message : String(error),
        kind: 'error'
      })
    } finally {
      setProgress(null)
      releaseRun(TOOL)
    }
  }

  return (
    <div className="patcher">
      <DiscDrop
        file={file}
        busy={busy}
        onChoose={(chosenFile) => {
          setFile(chosenFile)
          setStatus(null)
        }}
      />

      <fieldset className="patch-options" disabled={busy}>
        <p className="cheat-actions">
          <button type="button" className="btn" onClick={toggleAll}>
            {anyCheat ? 'Disable all cheats' : 'Enable all cheats'}
          </button>
        </p>
        {catalog.cheats.map((cheat) => {
          const locked = cheat.name === antiCheat && Boolean(needsAntiCheat)
          return (
            <label key={cheat.name} className="patch-option">
              <input
                type="checkbox"
                checked={selected.has(cheat.name)}
                disabled={locked}
                onChange={(event) => toggle(cheat.name, event.target.checked)}
              />
              <span>
                <strong>{cheat.title}</strong>
                <small>
                  {locked
                    ? `${needsAntiCheat!.title} needs it, so it stays on.`
                    : cheat.summary}
                </small>
              </span>
            </label>
          )
        })}
        {selected.has(antiCheat) && (
          <label className="patch-option">
            <input
              type="checkbox"
              checked={corruptSave}
              onChange={(event) => setCorruptSave(event.target.checked)}
            />
            <span>
              <strong>Corrupt-save</strong>
              <small>
                Off for an OPL build; on keeps the corrupted-save and
                memory-card recovery words.
              </small>
            </span>
          </label>
        )}
      </fieldset>

      <p className="row">
        <button
          type="button"
          className="btn accent"
          disabled={!file || !anyCheat || running !== null}
          onClick={create}
        >
          Create ISO with cheats
        </button>
      </p>
      <RunElsewhere tool={TOOL} />

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
