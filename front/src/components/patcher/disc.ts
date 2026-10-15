type SavePicker = (options: {
  suggestedName: string
  types: { description: string; accept: Record<string, string[]> }[]
}) => Promise<FileSystemFileHandle>

export type Status = { text: string; kind?: 'error' | 'success' }

const savePicker = (window as unknown as { showSaveFilePicker?: SavePicker })
  .showSaveFilePicker

export function megabytes(bytes: number): string {
  return `${(bytes / 1e6).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`
}

export function gigabytes(bytes: number): string {
  return `${(bytes / 1e9).toLocaleString(undefined, { maximumFractionDigits: 1 })} GB`
}

export async function chooseSaveHandle(
  suggestedName: string
): Promise<FileSystemFileHandle | null | undefined> {
  if (!savePicker) return null
  try {
    return await savePicker({
      suggestedName,
      types: [
        {
          description: 'Disc image',
          accept: { 'application/octet-stream': ['.iso'] }
        }
      ]
    })
  } catch {
    return undefined
  }
}

export function saveBlob(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10 * 60 * 1000)
}
