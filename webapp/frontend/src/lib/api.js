/**
 * API client. Same-origin /api (the FastAPI server serves this app; the Vite
 * dev server proxies /api to it). GETs are de-duplicated while in flight and
 * cached briefly — the server's data changes only when the pipeline republishes
 * its snapshot (about every 30 minutes).
 */

const CACHE_MS = 60_000
const cache = new Map()

function url(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  ).toString()
  return `/api/${path}${qs ? `?${qs}` : ''}`
}

async function getJson(u) {
  const hit = cache.get(u)
  if (hit && (hit.pending || Date.now() - hit.at < CACHE_MS)) return hit.promise
  const promise = fetch(u).then(async (res) => {
    let body = null
    try {
      body = await res.json()
    }
    catch {
      body = null
    }
    if (!res.ok) throw new Error(body?.error || `Request failed (${res.status})`)
    return body
  })
  const entry = { promise, pending: true, at: Date.now() }
  cache.set(u, entry)
  promise.then(
    () => {
      entry.pending = false
      entry.at = Date.now()
    },
    () => cache.delete(u), // errors are never cached
  )
  return promise
}

/** Uncached: used by the data-version poll to notice a newly published snapshot. */
export const getMetaFresh = () => getJson(url('meta', { t: Date.now() }))

// `v` is the snapshot version: a new snapshot gives a new URL, so caches never serve stale data.
export const getDashboard = params => getJson(url('dashboard', params))
export const listDevelopers = params => getJson(url('developers', params))
export const getDeveloper = ({ id, v }) => getJson(url(`developers/${id}`, { v }))
export const listProjects = params => getJson(url('projects', params))
export const searchAll = params => getJson(url('search', params))
export const getProject = ({ id, v }) => getJson(url(`projects/${id}`, { v }))
export const getUnits = ({ id, ...params }) => getJson(url(`projects/${id}/units`, params))
