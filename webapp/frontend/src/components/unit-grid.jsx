/**
 * Block-wise unit grid: every unit of every block as a tile (unit number, booked / unbooked, date of agreement),
 * with the full published details of a unit one click away.
 *
 * Same rules as the table view (units.jsx): status is shown as published, a status that was not captured reads
 * "Not captured" and is never drawn as unbooked, a booked unit without an agreement date says so instead of
 * guessing, and blocks whose unit rows were not captured are listed as such. There is no floor field in the source,
 * so units are never grouped into floors.
 */

import { useEffect, useMemo, useState } from 'react'

import { getUnits } from '../lib/api.js'
import { UNIT_STATUS_TONE, fmtArea, fmtDate, fmtInt, isKnown } from '../lib/format.js'

import { Badge, LoadError, SearchInput, SkeletonRows } from './ui.jsx'

const FETCH_SIZE = 500
const MAX_PAGES = 40
const STATUS_KEYS = ['booked', 'unbooked', 'other', 'unknown']
const STATUS_WORD = { booked: 'Booked', unbooked: 'Unbooked', other: 'Other status', unknown: 'Not captured' }
const FILTERS = [['', 'All'], ['booked', 'Booked'], ['unbooked', 'Unbooked'], ['other', 'Other status'], ['unknown', 'Not captured']]

const TILE_STYLE = {
  booked: { background: 'var(--accent-soft)', border: '1px solid var(--accent)', color: 'var(--ink)' },
  unbooked: { background: 'var(--card)', border: '1px dashed var(--line-strong)', color: 'var(--ink)' },
  other: { background: '#f4ebff', border: '1px solid var(--violet)', color: 'var(--ink)' },
  unknown: { background: 'var(--amber-soft)', border: '1px solid var(--amber)', color: 'var(--amber-ink)' },
}

const statusOf = u => (STATUS_KEYS.includes(u.status_norm) ? u.status_norm : 'unknown')

/** Loads every unit row of the project (the API caps a page at 500). */
function useAllUnits(id, enabled, version) {
  const [state, setState] = useState({ units: [], loading: false, error: null })
  useEffect(() => {
    if (!enabled) return undefined
    let cancelled = false
    ;(async () => {
      setState(s => ({ ...s, loading: true, error: null }))
      try {
        const all = []
        for (let page = 1; page <= MAX_PAGES; page += 1) {
          const r = await getUnits({ id, page, page_size: FETCH_SIZE, sort_by: 'unit', sort_dir: 'asc', v: version })
          if (cancelled) return
          all.push(...(r.units || []))
          if (!r.units?.length || all.length >= r.total) break
        }
        if (!cancelled) setState({ units: all, loading: false, error: null })
      } catch (e) {
        if (!cancelled) setState({ units: [], loading: false, error: e?.message || 'Could not load the units.' })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [id, enabled, version])
  return state
}

function count(units) {
  const c = { booked: 0, unbooked: 0, other: 0, unknown: 0, total: units.length }
  units.forEach((u) => {
    c[statusOf(u)] += 1
  })
  return c
}

function dateLine(u) {
  if (statusOf(u) !== 'booked') return null
  return u.agreement_date ? fmtDate(u.agreement_date) : 'Date not disclosed'
}

function UnitTile({ u, selected, onSelect, noun }) {
  const s = statusOf(u)
  const word = u.status && s === 'other' ? u.status : STATUS_WORD[s]
  const date = dateLine(u)
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`${noun} ${u.unit_no || 'N/A'}, ${word}${date ? `, ${date}` : ''}`}
      onClick={onSelect}
      style={{
        ...TILE_STYLE[s], textAlign: 'left', cursor: 'pointer', padding: '8px 10px', borderRadius: 'var(--radius-md)', minWidth: 0,
        outline: selected ? '2px solid var(--ink)' : 'none', outlineOffset: 1, display: 'flex', flexDirection: 'column', gap: 2,
      }}
    >
      <b style={{ fontSize: 14, overflowWrap: 'anywhere' }}>{u.unit_no || 'N/A'}</b>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{word}</span>
      <span className="body muted" style={{ fontSize: 11.5 }}>{date || ' '}</span>
    </button>
  )
}

function Detail({ u, plot, noun, onClose }) {
  const s = statusOf(u)
  const tone = UNIT_STATUS_TONE[s]
  const rows = [
    [plot ? 'Scheme / block' : 'Block', u.block_name],
    [`${noun[0].toUpperCase()}${noun.slice(1)} no.`, `${u.unit_no || 'N/A'}${u.unit_kind && u.unit_kind !== 'single' ? ` (filed as a ${u.unit_kind})` : ''}`],
    ...(plot ? [] : [['Usage', u.usage || 'N/A']]),
    [plot ? 'Plot area (sq m)' : 'Carpet area (sq m)', fmtArea(u.carpet_area_sqm)],
    ...(plot ? [] : [['Excl. balcony / veranda & open terrace (sq m)', fmtArea(u.area_excl_balcony_terrace_sqm)]]),
    ['Date of agreement', s === 'booked' && !u.agreement_date ? 'Not disclosed by the promoter' : fmtDate(u.agreement_date)],
    ['Encumbrance', u.encumbrance_status || 'N/A'],
    ...(plot ? [] : [['Redevelopment', u.redeveloped || 'N/A']]),
  ]
  return (
    <div role="region" aria-label="Selected unit" style={{ margin: '0 0 16px', padding: '12px 14px', borderRadius: 'var(--radius-md)', border: '1px solid var(--line)', background: 'var(--sunken)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <b style={{ color: 'var(--ink)' }}>{`${noun[0].toUpperCase()}${noun.slice(1)} ${u.unit_no || 'N/A'}`}</b>
        <Badge fill={tone.fill} ink={tone.ink}>{u.status || 'Not captured'}</Badge>
        <button type="button" onClick={onClose} style={{ marginLeft: 'auto', cursor: 'pointer', background: 'none', border: 'none', color: 'var(--ink-3)', fontSize: 13 }}>Close</button>
      </div>
      <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: '8px 16px', margin: 0 }}>
        {rows.map(([k, v]) => (
          <div key={k} style={{ minWidth: 0 }}>
            <dt className="body muted" style={{ fontSize: 12 }}>{k}</dt>
            <dd style={{ margin: 0, color: 'var(--ink)', overflowWrap: 'anywhere' }}>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function Legend() {
  return (
    <div role="group" aria-label="Legend" className="body muted" style={{ display: 'flex', flexWrap: 'wrap', gap: 14, margin: '0 0 12px', fontSize: 12.5 }}>
      {STATUS_KEYS.map(k => (
        <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span aria-hidden style={{ ...TILE_STYLE[k], width: 14, height: 14, borderRadius: 3, display: 'inline-block' }} />
          {STATUS_WORD[k]}
        </span>
      ))}
    </div>
  )
}

function BlockSection({ name, units, shown, selectedKey, onSelect, noun, plot }) {
  const c = count(units)
  const seg = (n, color) => (n ? <span style={{ width: `${(n / c.total) * 100}%`, background: color }} /> : null)
  return (
    <section aria-label={`Block ${name}`} style={{ margin: '0 0 22px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <h3 style={{ margin: 0, fontSize: 15, color: 'var(--ink)' }}>{plot ? name : `Block ${name}`}</h3>
        <span className="body muted">
          {`${fmtInt(c.total)} ${noun}s · `}
          <b>{fmtInt(c.booked)}</b>
          {' booked · '}
          <b>{fmtInt(c.unbooked)}</b>
          {' unbooked'}
          {c.other + c.unknown ? ` · ${fmtInt(c.other + c.unknown)} other / not captured` : ''}
        </span>
      </div>
      <div aria-hidden style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', background: 'var(--sunken)', marginBottom: 10 }}>
        {seg(c.booked, 'var(--accent)')}
        {seg(c.unbooked, 'var(--ink-4)')}
        {seg(c.other + c.unknown, 'var(--violet)')}
      </div>
      {shown.length
        ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(112px, 1fr))', gap: 8 }}>
              {shown.map(({ u, key }) => (
                <UnitTile key={key} u={u} noun={noun} selected={selectedKey === key} onSelect={() => onSelect(selectedKey === key ? null : key)} />
              ))}
            </div>
          )
        : <p className="body muted" style={{ margin: 0 }}>{`No ${noun}s in this block match the filters.`}</p>}
    </section>
  )
}

/**
 * `blocks`: project.blocks (order + which blocks have unit rows); `block`: block picked in the block cards ('' = all).
 */
export function UnitGrid({ project, blocks, block, version }) {
  const plot = project.category === 'plotted'
  const noun = plot ? 'plot' : 'unit'
  const { units, loading, error } = useAllUnits(project.project_id, true, version)
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [selectedKey, setSelectedKey] = useState(null)

  const groups = useMemo(() => {
    const by = new Map()
    units.forEach((u, i) => {
      if (!by.has(u.block_name)) by.set(u.block_name, [])
      by.get(u.block_name).push({ u, key: `${u.block_name}|${i}` })
    })
    const order = [...(blocks || []).map(b => b.block_name), ...by.keys()]
    return [...new Set(order)].map(name => ({ name, items: by.get(name) || [] }))
  }, [units, blocks])

  const q = search.trim().toLowerCase()
  const visible = groups.filter(g => !block || g.name === block)
  const all = visible.flatMap(g => g.items.map(i => i.u))
  const totals = count(all)
  const selected = visible.flatMap(g => g.items).find(i => i.key === selectedKey)
  const notCaptured = new Set((blocks || []).filter(b => !isKnown(b.unit_rows)).map(b => b.block_name))

  return (
    <div style={{ padding: '0 20px 16px' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 12 }}>
        <SearchInput id="unit-grid-search" value={search} onChange={setSearch} placeholder={`Search ${noun} number...`} label={`Search ${noun} number`} />
        <div role="group" aria-label="Booking status" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {FILTERS.map(([key, label]) => {
            const n = key ? totals[key] : totals.total
            if (key && !n && status !== key) return null
            const active = status === key
            return (
              <button
                key={key || 'all'}
                type="button"
                aria-pressed={active}
                onClick={() => setStatus(key)}
                style={{
                  padding: '4px 12px', borderRadius: 999, cursor: 'pointer', fontSize: 12.5, fontWeight: active ? 650 : 500,
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--line-strong)'}`, background: active ? 'var(--accent-soft)' : 'var(--card)',
                  color: active ? 'var(--accent)' : 'var(--ink-2)',
                }}
              >
                {`${label} · ${fmtInt(n)}`}
              </button>
            )
          })}
        </div>
      </div>
      <Legend />
      {error && <LoadError>{error}</LoadError>}
      {selected && <Detail u={selected.u} plot={plot} noun={noun} onClose={() => setSelectedKey(null)} />}
      {loading && !units.length
        ? <SkeletonRows rows={6} label="Loading units…" />
        : visible.map((g) => {
            if (!g.items.length) {
              return (
                <section key={g.name} aria-label={`Block ${g.name}`} style={{ margin: '0 0 22px' }}>
                  <h3 style={{ margin: '0 0 4px', fontSize: 15, color: 'var(--ink)' }}>{plot ? g.name : `Block ${g.name}`}</h3>
                  <p className="body muted" style={{ margin: 0 }}>
                    {notCaptured.has(g.name) ? `Unit rows were not captured for this block. Its ${noun}s are not counted as unbooked.` : `No ${noun} list is published for this block.`}
                  </p>
                </section>
              )
            }
            const shown = g.items.filter(({ u }) => (!status || statusOf(u) === status) && (!q || String(u.unit_no || '').toLowerCase().includes(q)))
            return (
              <BlockSection
                key={g.name}
                name={g.name}
                units={g.items.map(i => i.u)}
                shown={shown}
                selectedKey={selectedKey}
                onSelect={setSelectedKey}
                noun={noun}
                plot={plot}
              />
            )
          })}
    </div>
  )
}
