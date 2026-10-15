import { useCallback } from 'react'
import { create } from 'zustand'

type RunState = {
  running: string | null
  panels: Record<string, Record<string, unknown>>
}

export const runStore = create<RunState>()(() => ({
  running: null,
  panels: {}
}))

export function usePanelState<T>(
  tool: string,
  key: string,
  initial: T
): [T, (value: T) => void] {
  const panel = runStore((state) => state.panels[tool])
  const value = panel && key in panel ? (panel[key] as T) : initial
  const set = useCallback(
    (next: T) =>
      runStore.setState((state) => ({
        panels: {
          ...state.panels,
          [tool]: { ...state.panels[tool], [key]: next }
        }
      })),
    [tool, key]
  )
  return [value, set]
}

export function claimRun(tool: string): boolean {
  if (runStore.getState().running !== null) return false
  runStore.setState({ running: tool })
  return true
}

export function releaseRun(tool: string) {
  if (runStore.getState().running === tool) runStore.setState({ running: null })
}

window.addEventListener('beforeunload', (event) => {
  if (runStore.getState().running !== null) event.preventDefault()
})
