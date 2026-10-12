/**
 * Shared UI primitives. Surfaces are separated by a hairline, never a shadow;
 * text always wears a text token, and colour is reserved for data marks and
 * status (always paired with a label).
 */

import { STATUS_TONE, fmtInt } from '../lib/format.js'

/** UTC ISO timestamp -> local "08 Oct 2026, 11:42". */
function fmtStamp(iso) {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return 'N/A'
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function Page({ children }) {
  return <div style={{ padding: '28px 32px 56px', maxWidth: 1440 }}>{children}</div>
}

export function PageHeader({ title, subtitle, back }) {
  return (
    <header style={{ marginBottom: 16 }}>
      {back}
      <h1 className="page-title" style={{ marginTop: back ? 8 : 0 }}>{title}</h1>
      {subtitle && <p className="body muted" style={{ margin: '2px 0 0' }}>{subtitle}</p>}
    </header>
  )
}

export function Card({ title, subtitle, action, children, style }) {
  return (
    <section style={{ background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', minWidth: 0, ...style }}>
      {(title || action) && (
        <header style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', padding: '16px 20px 12px' }}>
          <div style={{ minWidth: 0 }}>
            {title && <h2 className="section-title">{title}</h2>}
            {subtitle && <p className="body muted" style={{ margin: '2px 0 0' }}>{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function StatTile({ label, value, note }) {
  return (
    <div style={{ flex: '1 1 160px', minWidth: 150, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: 16 }}>
      <div className="body-strong muted">{label}</div>
      <div className="stat-number" style={{ marginTop: 4 }}>{value}</div>
      {note && <div className="body muted" style={{ marginTop: 2 }}>{note}</div>}
    </div>
  )
}

export function Kpis({ items }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
      {items.map(([label, value, note]) => <StatTile key={label} label={label} value={value} note={note} />)}
    </div>
  )
}

export function Badge({ children, fill = 'var(--sunken)', ink = 'var(--ink-2)' }) {
  return (
    <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 'var(--radius-sm)', background: fill, color: ink, fontSize: 12, lineHeight: '18px', fontWeight: 550, whiteSpace: 'nowrap' }}>
      {children}
    </span>
  )
}

export function StatusBadge({ status }) {
  const tone = STATUS_TONE[status] || STATUS_TONE.Unknown
  return <Badge fill={tone.fill} ink={tone.ink}>{status || 'Unknown'}</Badge>
}

export function Segmented({ options, value, onChange, label }) {
  return (
    <div role="tablist" aria-label={label} style={{ display: 'inline-flex', padding: 2, gap: 2, background: 'var(--sunken)', border: '1px solid var(--line)', borderRadius: 'var(--radius-md)' }}>
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          style={{
            padding: '4px 12px', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12.5,
            background: o.value === value ? 'var(--card)' : 'transparent',
            color: o.value === value ? 'var(--ink)' : 'var(--ink-3)', fontWeight: o.value === value ? 650 : 450,
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function FilterSelect({ value, onChange, options, ariaLabel, id }) {
  return (
    <select
      id={id}
      aria-label={ariaLabel}
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{ padding: '6px 10px', borderRadius: 'var(--radius-md)', border: '1px solid var(--line-strong)', background: 'var(--card)', color: 'var(--ink-2)', fontSize: 12.5, maxWidth: 220 }}
    >
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

export function LoadError({ children }) {
  return (
    <div role="alert" className="body" style={{ margin: '0 0 16px', padding: '10px 14px', background: 'var(--danger-soft)', border: '1px solid var(--line)', borderRadius: 'var(--radius-md)', color: 'var(--danger-ink)' }}>
      {children}
    </div>
  )
}

export function Empty({ children }) {
  return <div className="body muted" style={{ padding: '36px 16px', textAlign: 'center' }}>{children}</div>
}

const SKELETON_KEYS = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l']

export function SkeletonRows({ rows = 6, label = 'Loading…' }) {
  return (
    <div role="status" aria-label={label} style={{ padding: 16, display: 'grid', gap: 10 }}>
      <span className="body muted">{label}</span>
      {SKELETON_KEYS.slice(0, rows).map(k => <div key={k} className="skeleton" style={{ height: 18, borderRadius: 4 }} />)}
    </div>
  )
}

/**
 * How complete the data is. While the crawl runs the app must not read as the
 * whole Ahmedabad market, so the label changes with `dataset_complete` and the
 * analysed / pending counts are always shown.
 */
export function DatasetStatus({ meta }) {
  if (!meta) return null
  const complete = meta.dataset_complete
  const state = {
    running: 'Data collection running',
    paused: 'Data collection paused',
    completed: 'Data collection complete',
    not_started: 'Data collection not started',
  }[meta.crawl_state] || 'Data collection status unknown'
  return (
    <div
      className="body"
      style={{
        display: 'flex', flexWrap: 'wrap', gap: '6px 20px', alignItems: 'center', padding: '10px 14px', marginBottom: 16,
        borderRadius: 'var(--radius-md)', border: `1px solid ${complete ? 'var(--line)' : 'var(--amber)'}`,
        background: complete ? 'var(--card)' : 'var(--amber-soft)',
      }}
    >
      <strong style={{ color: complete ? 'var(--ink)' : 'var(--amber-ink)' }}>{complete ? 'Complete dataset' : 'Partial dataset'}</strong>
      <span>
        Projects analysed
        {' '}
        <b>{fmtInt(meta.projects_analyzed)}</b>
        {' of '}
        {fmtInt(meta.projects_total)}
      </span>
      {!complete && (
        <span>
          Pending
          {' '}
          <b>{fmtInt(meta.projects_pending)}</b>
        </span>
      )}
      <span>
        Developers identified
        {' '}
        <b>{fmtInt(meta.developers_identified)}</b>
      </span>
      <span>{state}</span>
      <span>
        Last updated
        {' '}
        {fmtStamp(meta.generated_at)}
      </span>
      {!complete && <span className="muted">New data appears automatically</span>}
    </div>
  )
}

const TH = { textAlign: 'left', padding: '10px 12px', whiteSpace: 'nowrap', borderBottom: '1px solid var(--line)', background: 'var(--card)', position: 'sticky', top: 0 }
const TD = { padding: '10px 12px', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap', verticalAlign: 'top' }

/** A plain table. columns: { key, label, align, render(row), sortable }. Sorting is controlled by the caller. */
export function DataTable({ columns, rows, rowKey, sort, onSort, onRowClick, rowLabel, empty }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.by === c.key
              return (
                <th key={c.key} className="table-header" aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined} style={{ ...TH, textAlign: c.align || 'left' }}>
                  {c.sortable && onSort
                    ? (
                        <button type="button" onClick={() => onSort(c.key)} style={{ all: 'unset', cursor: 'pointer', color: active ? 'var(--ink)' : 'inherit' }}>
                          {c.label}
                          {active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                        </button>
                      )
                    : c.label}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={columns.length} style={{ ...TD, textAlign: 'center', padding: 32, color: 'var(--ink-3)' }}>{empty}</td></tr>
          )}
          {rows.map(r => (
            <tr
              key={rowKey(r)}
              tabIndex={onRowClick ? 0 : undefined}
              aria-label={onRowClick && rowLabel ? rowLabel(r) : undefined}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === 'Enter') onRowClick(r) } : undefined}
              onMouseEnter={(e) => { if (onRowClick) e.currentTarget.style.background = 'var(--hover)' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
              style={{ cursor: onRowClick ? 'pointer' : 'default' }}
            >
              {columns.map(c => (
                <td key={c.key} style={{ ...TD, textAlign: c.align || 'left' }}>{c.render ? c.render(r) : r[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SmallButton({ children, onClick, disabled, primary }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        border: `1px solid ${primary && !disabled ? 'var(--ink)' : 'var(--line-strong)'}`, borderRadius: 'var(--radius-md)', padding: '5px 12px',
        background: primary && !disabled ? 'var(--ink)' : 'var(--card)', color: disabled ? 'var(--ink-4)' : primary ? 'var(--on-dark)' : 'var(--ink-2)',
        cursor: disabled ? 'default' : 'pointer', fontSize: 12.5, fontWeight: 550,
      }}
    >
      {children}
    </button>
  )
}
