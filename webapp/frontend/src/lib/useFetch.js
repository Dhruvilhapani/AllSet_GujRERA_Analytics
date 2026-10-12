import { useEffect, useState } from 'react'

/**
 * `fetcher(params)`, re-run whenever `params` changes by value. Pass a falsy
 * `params` to skip the request. The previous data stays on screen while the
 * next request loads; an error only shows for the request that produced it.
 * `fetcher` must be a stable module-level function.
 */
export default function useFetch(fetcher, params, initial = null) {
  const key = params ? JSON.stringify(params) : null
  const [state, setState] = useState({ key: null, data: initial, error: null })

  useEffect(() => {
    if (key == null) return undefined
    let cancelled = false
    fetcher(JSON.parse(key)).then(
      (data) => {
        if (!cancelled) setState({ key, data, error: null })
      },
      (err) => {
        if (!cancelled) setState(s => ({ key, data: s.data, error: err.message }))
      },
    )
    return () => {
      cancelled = true
    }
  }, [fetcher, key])

  if (key == null) return { data: initial, loading: false, error: null }
  const current = state.key === key
  return { data: state.data, loading: !current, error: current ? state.error : null }
}
