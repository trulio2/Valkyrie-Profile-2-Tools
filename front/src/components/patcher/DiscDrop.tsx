import { useState } from 'react'

type DiscDropProps = {
  file: File | null
  busy: boolean
  onChoose: (file: File) => void
}

export default function DiscDrop({ file, busy, onChoose }: DiscDropProps) {
  const [dragging, setDragging] = useState(false)

  return (
    <label
      className={`patch-drop${dragging ? ' drag' : ''}${busy ? ' busy' : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        const dropped = event.dataTransfer.files[0]
        if (!busy && dropped) onChoose(dropped)
      }}
    >
      <input
        type="file"
        accept=".iso"
        disabled={busy}
        onChange={(event) => {
          const chosen = event.target.files?.[0]
          if (chosen) onChoose(chosen)
        }}
      />
      {file ? file.name : 'Choose your disc image, or drop it here'}
    </label>
  )
}
