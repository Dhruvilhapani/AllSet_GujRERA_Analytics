/**
 * Block Booking Details + Unit Wise Booking Status (the GujRERA project page sections).
 *
 * Rules the UI keeps: the status is shown exactly as published; a status that was not captured reads "Not captured"
 * and is never counted or drawn as unbooked; unknown areas / dates read N/A while a published 0 reads 0; totals are
 * labelled with their source (unit rows of the latest Form 3 vs the portal's block chart, which can differ).
 */

import { useEffect, useState } from 'react'

import { getUnits } from '../lib/api.js'
import { UNIT_STATE_TEXT, UNIT_STATUS_TONE, fmtArea, fmtDate, fmtInt, fmtPct, isKnown } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

import { UnitGrid } from './unit-grid.jsx'
import { Badge, Card, DataTable, FilterSelect, LoadError, Pager, SearchInput, SkeletonRows } from './ui.jsx'

const PAGE_SIZE = 100
const NO_UNITS = { units: [], total: 0, page: 1, page_size: PAGE_SIZE, summary: { booked: 0, unbooked: 0, other: 0, unknown: 0, total: 0 }, usage_classes: [] }
const USAGE_LABEL = { residential: 'Residential', shop: 'Shop', office: 'Office', plot: 'Plot', other: 'Other' }
const STATUS_CHIPS = [['', 'All'], ['booked', 'Booked'], ['unbooked', 'Unbooked'], ['other', 'Other status'], ['unknown', 'Not captured']]
const SORT_KEY = { unit_no: 'unit', usage: 'usage', carpet_area_sqm: 'carpet', area_excl_balcony_terrace_sqm: 'area', status: 'status', agreement_date: 'agreement' }
const COLUMN_FOR_SORT = Object.fromEntries(Object.entries(SORT_KEY).map(([c, s]) => [s, c]))

export function StateBanner({ state, note, asOf }) {
  if (!state || state === 'complete') return null
  const bad = state === 'partial' || state === 'error'
  return (
    <div
      role="status"
      className="body"
      style={{ margin: '0 0 14px', padding: '10px 14px', borderRadius: 'var(--radius-md)', border: `1px solid ${bad ? 'var(--amber)' : 'var(--line)'}`, background: bad ? 'var(--amber-soft)' : 'var(--sunken)', color: bad ? 'var(--amber-ink)' : 'var(--ink-2)' }}
    >
      <b>{UNIT_STATE_TEXT[state] || 'Unit data status unknown.'}</b>
      {note ? ` ${note}.` : ''}
      {asOf && state === 'partial' ? ` Rows shown are from the Form 3 submitted on ${fmtDate(asOf)}.` : ''}
      {' '}
      Units that were not captured are not counted as unbooked.
    </div>
  )
}

function StatusBadge({ row }) {
  const tone = UNIT_STATUS_TONE[row.status_norm] || UNIT_STATUS_TONE.unknown
  return <Badge fill={tone.fill} ink={tone.ink}>{row.status || 'Not captured'}</Badge>
}

function Bar({ booked, unbooked, other }) {
  const total = (booked || 0) + (unbooked || 0) + (other || 0)
  if (!total) return null
  const seg = (n, color) => (n ? <span style={{ width: `${(n / total) * 100}%`, background: color }} /> : null)
  return (
    <div aria-hidden style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', background: 'var(--sunken)', marginTop: 8 }}>
      {seg(booked, 'var(--accent)')}
      {seg(unbooked, 'var(--ink-4)')}
      {seg(other, 'var(--violet)')}
    </div>
  )
}

/** Block cards: counts from the unit rows (latest Form 3) with the portal's block-chart counts alongside. */
export function BlockCards({ blocks, selected, onSelect, totalRows }) {
  const withUnits = blocks.filter(b => isKnown(b.unit_rows))
  if (!blocks.length) return null
  const card = (key, title, body, active, onClick) => (
    <button
      key={key}
      type="button"
      aria-pressed={active}
      onClick={onClick}
      style={{
        textAlign: 'left', cursor: 'pointer', padding: '12px 14px', minWidth: 150, flex: '1 1 150px', maxWidth: 260,
        borderRadius: 'var(--radius-md)', background: active ? 'var(--accent-soft)' : 'var(--card)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--line)'}`, color: 'var(--ink-2)',
      }}
    >
      <div style={{ fontWeight: 650, color: 'var(--ink)' }}>{title}</div>
      {body}
    </button>
  )
  return (
    <div role="group" aria-label="Blocks" style={{ display: 'flex', flexWrap: 'wrap', gap: 10, margin: '0 0 16px' }}>
      {card('all', 'All blocks', <div className="body muted">{`${fmtInt(totalRows ?? withUnits.reduce((s, b) => s + b.unit_rows, 0))} unit rows`}</div>, selected === '', () => onSelect(''))}
      {blocks.map((b) => {
        const known = isKnown(b.unit_rows)
        const chart = isKnown(b.sold_units) || isKnown(b.unsold_units)
        const differs = known && chart && (b.sold_units !== b.unit_booked || b.unsold_units !== b.unit_unbooked)
        return card(b.block_name, `Block ${b.block_name}`, (
          <>
            {known
              ? (
                  <div className="body">
                    {`${fmtInt(b.unit_rows)} units · `}
                    <b>{fmtInt(b.unit_booked)}</b>
                    {' booked · '}
                    <b>{fmtInt(b.unit_unbooked)}</b>
                    {' unbooked'}
                    {b.unit_other ? ` · ${fmtInt(b.unit_other)} other / not captured` : ''}
                  </div>
                )
              : <div className="body muted">{b.fetch_outcome === 'empty' ? 'No unit list published' : 'Unit rows not captured'}</div>}
            {known && <Bar booked={b.unit_booked} unbooked={b.unit_unbooked} other={b.unit_other} />}
            {chart && (
              <div className="body muted" style={{ marginTop: 6 }}>
                {`Portal booking chart: ${fmtInt(b.sold_units)} booked / ${fmtInt(b.unsold_units)} unbooked`}
                {differs ? ' (differs from the unit rows; the two are filed separately)' : ''}
              </div>
            )}
            {(isKnown(b.floors) || isKnown(b.lifts) || isKnown(b.height) || isKnown(b.fsi) || isKnown(b.builtup_area_sqm)) && (
              <div className="body muted">
                {[isKnown(b.floors) ? `${fmtInt(b.floors)} floors` : null, isKnown(b.lifts) ? `${fmtInt(b.lifts)} lifts` : null, isKnown(b.height) ? `Height ${fmtArea(b.height)} m` : null, isKnown(b.fsi) ? `FSI ${fmtArea(b.fsi)}` : null, isKnown(b.builtup_area_sqm) ? `Built-up ${fmtArea(b.builtup_area_sqm)} sq m` : null].filter(Boolean).join(' · ')}
              </div>
            )}
          </>
        ), selected === b.block_name, () => onSelect(b.block_name))
      })}
    </div>
  )
}

function StatusChips({ summary, value, onChange }) {
  return (
    <div role="group" aria-label="Booking status" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {STATUS_CHIPS.map(([key, label]) => {
        const n = key ? summary[key] : summary.total
        if (key && !n && value !== key) return null
        const active = value === key
        return (
          <button
            key={key || 'all'}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(key)}
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
  )
}

function ViewToggle({ value, onChange }) {
  return (
    <div role="group" aria-label="View" style={{ display: 'inline-flex', border: '1px solid var(--line-strong)', borderRadius: 999, overflow: 'hidden' }}>
      {[['grid', 'Block grid'], ['table', 'Table']].map(([key, label]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          style={{
            padding: '4px 14px', cursor: 'pointer', fontSize: 12.5, fontWeight: value === key ? 650 : 500, border: 'none',
            background: value === key ? 'var(--accent-soft)' : 'var(--card)', color: value === key ? 'var(--accent)' : 'var(--ink-2)',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

export function UnitsTab({ project, version, initialView = 'grid' }) {
  const plot = project.category === 'plotted'
  const [view, setView] = useState(initialView)
  const [block, setBlock] = useState('')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [usage, setUsage] = useState('')
  const [sort, setSort] = useState({ by: 'unit', dir: 'asc' })
  const [page, setPage] = useState(1)

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [search])

  const state = project.unit_data_state
  const usable = state === 'complete' || state === 'partial'
  const res = useFetch(
    getUnits,
    usable && project.units_available && view === 'table' ? { id: project.project_id, block, q, status, usage, sort_by: sort.by, sort_dir: sort.dir, page, page_size: PAGE_SIZE, v: version } : null,
    NO_UNITS,
  )
  const data = res.data || NO_UNITS
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE))
  const noun = plot ? 'plot' : 'unit'
  const reset = fn => (v) => {
    fn(v)
    setPage(1)
  }

  const columns = [
    {
      key: 'unit_no',
      label: plot ? 'Plot no.' : 'Unit no.',
      sortable: true,
      render: r => (
        <span title={r.unit_kind === 'range' ? 'The promoter filed a range of units as one row; it is shown as published.' : undefined}>
          <b style={{ color: 'var(--ink)' }}>{r.unit_no || 'N/A'}</b>
          {r.unit_kind && r.unit_kind !== 'single' ? <span className="body muted">{` (${r.unit_kind})`}</span> : null}
        </span>
      ),
    },
    { key: 'block_name', label: plot ? 'Scheme / block' : 'Block', render: r => r.block_name },
    ...(plot ? [] : [{ key: 'usage', label: 'Usage', sortable: true, render: r => r.usage || 'N/A' }]),
    { key: 'carpet_area_sqm', label: plot ? 'Plot area (sq m)' : 'Carpet area (sq m)', align: 'right', sortable: true, render: r => fmtArea(r.carpet_area_sqm) },
    ...(plot
      ? []
      : [{
          key: 'area_excl_balcony_terrace_sqm',
          label: (<span title="One figure as published by GujRERA: exclusive balcony/veranda and exclusive open terrace together. It is not split.">Excl. balcony / veranda &amp; open terrace (sq m)</span>),
          align: 'right',
          sortable: true,
          render: r => fmtArea(r.area_excl_balcony_terrace_sqm),
        }]),
    { key: 'status', label: 'Status', sortable: true, render: r => <StatusBadge row={r} /> },
    { key: 'agreement_date', label: 'Date of agreement', sortable: true, render: r => fmtDate(r.agreement_date) },
    { key: 'encumbrance_status', label: 'Encumbrance', render: r => r.encumbrance_status || 'N/A' },
    ...(plot ? [] : [{ key: 'redeveloped', label: 'Redevelopment', render: r => r.redeveloped || 'N/A' }]),
  ]

  return (
    <>
      <StateBanner state={state} note={project.unit_data_note} asOf={project.units_as_of} />
      {!project.units_available && (
        <LoadError>Unit-wise data is not part of this build of the app, so only block counts are shown.</LoadError>
      )}
      <Card
        title="Block booking details"
        subtitle={project.units_as_of ? `Latest Form 3 submitted on ${fmtDate(project.units_as_of)}. GujRERA publishes only the latest status, so there is no booking history.` : 'Booking counts per block.'}
      >
        <div style={{ padding: '0 20px 4px' }}>
          <BlockCards blocks={project.blocks || []} selected={block} onSelect={reset(setBlock)} totalRows={usable ? project.units_row_count : null} />
        </div>
      </Card>
      {usable && project.units_available && (
        <Card
          title={plot ? 'Plot wise booking status' : 'Unit wise booking status'}
          subtitle={state === 'partial' ? 'Only the captured blocks are listed.' : 'Every unit as filed in the latest Form 3.'}
          action={<ViewToggle value={view} onChange={setView} />}
          style={{ marginTop: 16 }}
        >
          {view === 'grid' && <UnitGrid project={project} blocks={project.blocks || []} block={block} version={version} />}
          {view === 'table' && <>
          <div style={{ padding: '0 20px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 12 }}>
              <SearchInput id="unit-search" value={search} onChange={setSearch} placeholder={`Search ${noun} number...`} label={`Search ${noun} number`} />
              {!plot && data.usage_classes.length > 1 && (
                <FilterSelect
                  id="unit-usage"
                  ariaLabel="Usage"
                  value={usage}
                  onChange={reset(setUsage)}
                  options={[{ value: '', label: 'All usage' }, ...data.usage_classes.map(u => ({ value: u, label: USAGE_LABEL[u] || u }))]}
                />
              )}
              <StatusChips summary={data.summary} value={status} onChange={reset(setStatus)} />
            </div>
            {res.error && <LoadError>{res.error}</LoadError>}
            {state === 'complete' && data.summary.total > 0 && (
              <p className="body muted" style={{ margin: '0 0 10px' }}>
                {`${fmtInt(data.summary.booked)} of ${fmtInt(data.summary.total)} booked (${fmtPct(data.summary.total ? (100 * data.summary.booked) / data.summary.total : null, 1)})${block ? ` in block ${block}` : ''}.`}
              </p>
            )}
          </div>
          {res.loading && !data.units.length
            ? <SkeletonRows rows={8} label="Loading units…" />
            : (
                <div style={{ opacity: res.loading ? 0.6 : 1, transition: 'opacity .15s' }}>
                  <DataTable
                    columns={columns}
                    rows={data.units}
                    rowKey={r => `${r.block_name}|${r.unit_no}|${r.carpet_area_sqm}|${r.agreement_date}|${r.status}`}
                    sort={{ by: COLUMN_FOR_SORT[sort.by] || sort.by, dir: sort.dir }}
                    onSort={(key) => {
                      const by = SORT_KEY[key]
                      setSort(s => ({ by, dir: s.by === by && s.dir === 'asc' ? 'desc' : 'asc' }))
                      setPage(1)
                    }}
                    empty={`No ${noun}s match these filters.`}
                  />
                </div>
              )}
          <Pager page={data.page} pages={pages} onPage={setPage} total={data.total} pageSize={PAGE_SIZE} noun={`${noun}s`} />
          </>}
        </Card>
      )}
      <p className="body muted" style={{ marginTop: 12 }}>
        Status is shown as published by GujRERA. The balcony / veranda and open-terrace area is one combined figure per unit;
        it is not split. Date of agreement is the date reported by the promoter, not necessarily the date the unit was first
        sold, and is blank where not reported. Amounts and buyer details are not published and are not shown.
      </p>
    </>
  )
}
