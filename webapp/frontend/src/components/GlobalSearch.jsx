/**
 * Global search (top bar, every page): type two or more letters to get live suggestions for projects (name or RERA
 * number), developers (name or alias) and localities. Every word must match, in any order. Keyboard: "/" focuses the box,
 * arrow keys move through the suggestions, Enter opens one (or lists every matching project when none is highlighted),
 * Esc closes. A group that has more matches than shown ends with a "See all N …" row that opens the filtered list.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { searchAll } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { CATEGORY_LABEL, fmtInt } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

const MIN_CHARS = 2
const SHOWN = 6

function Highlight({ text, words }) {
  if (!text || !words.length) return text || ''
  const rx = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig')
  return text.split(rx).map((part, i) => (
    // the split parts only differ by position, so the index is the identity here
    // eslint-disable-next-line react/no-array-index-key
    i % 2 ? <mark key={i} style={{ background: 'var(--amber-soft)', color: 'inherit', padding: 0 }}>{part}</mark> : part
  ))
}

/** Flat list of rows (for keyboard navigation) built from the API answer. */
export function buildItems(data, q) {
  if (!data || data.q === undefined) return []
  const enc = encodeURIComponent
  const items = []
  data.projects.forEach(p => items.push({
    key: `p${p.project_id}`, group: 'Projects', to: `/projects/${p.project_id}`, title: p.name,
    sub: [CATEGORY_LABEL[p.category], p.rera_number, [p.locality, p.city].filter(Boolean).join(', '), p.status].filter(Boolean).join(' · '),
    extra: p.developer_name,
  }))
  if (data.projects_total > data.projects.length) {
    items.push({ key: 'p-all', group: 'Projects', to: `/projects?q=${enc(q)}`, title: `See all ${fmtInt(data.projects_total)} matching projects`, more: true })
  }
  data.developers.forEach(d => items.push({
    key: `d${d.developer_id}`, group: 'Developers', to: `/developers/${d.developer_id}`, title: d.name,
    sub: `${fmtInt(d.projects_total)} project${d.projects_total === 1 ? '' : 's'}`,
  }))
  if (data.developers_total > data.developers.length) {
    items.push({ key: 'd-all', group: 'Developers', to: `/developers?q=${enc(q)}`, title: `See all ${fmtInt(data.developers_total)} matching developers`, more: true })
  }
  data.localities.forEach(l => items.push({
    key: `l${l.city}|${l.name}`, group: 'Localities', to: `/projects?locality=${enc(l.name)}${l.city ? `&city=${enc(l.city)}` : ''}`,
    title: l.name, sub: `${l.city ? `${l.city} · ` : ''}${fmtInt(l.projects)} project${l.projects === 1 ? '' : 's'}`,
  }))
  return items
}

export default function GlobalSearch() {
  const navigate = useNavigate()
  const { version } = useDataVersion()
  const id = useId()
  const input = useRef(null)
  const box = useRef(null)
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)

  useEffect(() => { // debounce typing so each keystroke is not a request
    const t = setTimeout(() => setQ(text.trim()), 180)
    return () => clearTimeout(t)
  }, [text])

  useEffect(() => { // "/" focuses the search from anywhere (unless the user is typing in a field)
    const onKey = (e) => {
      const tag = (e.target?.tagName || '').toLowerCase()
      if (e.key === '/' && !e.ctrlKey && !e.metaKey && !['input', 'textarea', 'select'].includes(tag) && !e.target?.isContentEditable) {
        e.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => { // click outside closes the list
    const onDown = (e) => {
      if (box.current && !box.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const enabled = q.length >= MIN_CHARS && version
  const { data, loading, error } = useFetch(searchAll, enabled ? { q, limit: SHOWN, v: version } : null)
  const fresh = data && data.q === q.split(/\s+/).join(' ').toLowerCase() ? data : null
  const items = useMemo(() => (enabled && fresh ? buildItems(fresh, q) : []), [enabled, fresh, q])
  const words = useMemo(() => q.toLowerCase().split(/\s+/).filter(Boolean), [q])
  const searching = Boolean(enabled && (loading || !fresh))

  const go = (to) => {
    setOpen(false)
    setActive(-1)
    navigate(to)
  }
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!items.length) return
      e.preventDefault()
      setOpen(true)
      setActive(a => (e.key === 'ArrowDown' ? (a + 1) % items.length : (a - 1 + items.length) % items.length))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0 && items[active]) go(items[active].to)
      else if (text.trim().length >= MIN_CHARS) go(`/projects?q=${encodeURIComponent(text.trim())}`)
    } else if (e.key === 'Escape') {
      setOpen(false)
      setActive(-1)
      input.current?.blur()
    }
  }

  const showList = open && q.length >= MIN_CHARS
  let lastGroup = null
  return (
    <div ref={box} style={{ position: 'relative', flex: '1 1 420px', maxWidth: 640 }}>
      <input
        ref={input}
        type="search"
        role="combobox"
        aria-label="Search projects, developers and localities"
        aria-expanded={showList}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 && items[active] ? `${id}-${items[active].key}` : undefined}
        autoComplete="off"
        placeholder="Search projects, RERA no., developers, localities…   ( / )"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
          setActive(-1)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        style={{ width: '100%', padding: '9px 14px', borderRadius: 'var(--radius-md)', border: '1px solid var(--line-strong)', background: 'var(--card)', color: 'var(--ink)', fontSize: 14 }}
      />
      {showList && (
        <div
          id={`${id}-list`}
          role="listbox"
          aria-label="Search results"
          style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0, minWidth: 'min(560px, 90vw)', maxHeight: '70vh', overflowY: 'auto', background: 'var(--card)', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius-md)', boxShadow: '0 8px 24px rgba(16,24,40,.12)', zIndex: 60 }}
        >
          <div role="status" aria-live="polite" className="body muted" style={{ padding: '8px 14px', borderBottom: '1px solid var(--line)' }}>
            {error ? `Search failed: ${error}` : searching ? 'Searching…' : items.length ? `${fmtInt(items.filter(i => !i.more).length)} suggestions` : `No projects, developers or localities match “${q}”.`}
          </div>
          {items.map((it, i) => {
            const header = it.group !== lastGroup
            lastGroup = it.group
            return (
              <div key={it.key}>
                {header && <div className="table-header" style={{ padding: '8px 14px 4px', textTransform: 'uppercase', letterSpacing: '.04em' }}>{it.group}</div>}
                <div
                  id={`${id}-${it.key}`}
                  role="option"
                  aria-selected={i === active}
                  tabIndex={-1}
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => go(it.to)}
                  onMouseEnter={() => setActive(i)}
                  style={{ padding: '7px 14px', cursor: 'pointer', background: i === active ? 'var(--accent-soft)' : 'transparent' }}
                >
                  <div style={{ color: it.more ? 'var(--accent)' : 'var(--ink)', fontWeight: it.more ? 600 : 550 }}>
                    {it.more ? it.title : <Highlight text={it.title} words={words} />}
                  </div>
                  {it.sub && <div className="body muted"><Highlight text={it.sub} words={words} /></div>}
                  {it.extra && <div className="body muted">{`by ${it.extra}`}</div>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
