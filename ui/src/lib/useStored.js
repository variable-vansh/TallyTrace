import { useCallback, useEffect, useState } from 'react'

// Per-browser preferences that survive a reload. Recipient addresses live here rather
// than in the run artifact: `ui/public/tallytrace.json` is rebuilt by `make ui-data`
// from a scored run and nothing a person types should be overwritten by a rebuild.
// Wrapped in try/catch because a private window or blocked site data makes even the
// read throw, and a claims queue that cannot render because a mailbox is unknown
// would be a worse failure than not knowing the mailbox.
export function useStored(key, fallback) {
  const [value, setValue] = useState(() => {
    try {
      const raw = window.localStorage.getItem(key)
      return raw === null ? fallback : JSON.parse(raw)
    } catch {
      return fallback
    }
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* nothing to do: the preference simply does not persist this session */
    }
  }, [key, value])

  const reset = useCallback(() => setValue(fallback), [fallback])
  return [value, setValue, reset]
}
