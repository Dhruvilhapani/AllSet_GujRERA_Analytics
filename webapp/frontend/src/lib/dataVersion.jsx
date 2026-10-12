/**
 * Live data version. Polls /api/meta every minute; when the pipeline publishes a
 * new snapshot, `version` (its generated_at stamp) changes and every page that
 * includes it in its fetch params reloads its data automatically.
 */

import { createContext, useContext, useEffect, useState } from 'react'

import { getMetaFresh } from './api.js'

const POLL_MS = 60_000
const DataVersionContext = createContext({ meta: null, version: null, error: null })

export function DataVersionProvider({ children }) {
  const [state, setState] = useState({ meta: null, error: null })
  useEffect(() => {
    let alive = true
    const load = () => getMetaFresh().then(
      meta => alive && setState({ meta, error: null }),
      err => alive && setState(s => ({ ...s, error: err.message })),
    )
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])
  const value = { meta: state.meta, version: state.meta?.generated_at ?? null, error: state.error }
  return <DataVersionContext.Provider value={value}>{children}</DataVersionContext.Provider>
}

export const useDataVersion = () => useContext(DataVersionContext)
