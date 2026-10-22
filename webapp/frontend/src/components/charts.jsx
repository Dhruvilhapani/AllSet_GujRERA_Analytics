/**
 * Charts (recharts). One series colour for single-series charts; validated categorical slots blue #2a78d6 /
 * orange #eb6834 only where two series are compared. Thin marks, solid recessive grid, no entry animation,
 * and every chart has a Table view so no value is readable only by hover.
 */

import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { fmtInt, fmtPct } from '../lib/format.js'

import { Card, Empty } from './ui.jsx'

// Categorical marks, in category order (residential, mixed, commercial, plotted); always paired with a legend label.
export const SERIES = ['#2a78d6', '#eb6834', '#1f9d8b', '#6941c6']
const RAMP = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281', '#0d366b']
const SURFACE = '#ffffff'
const GRID = { stroke: '#e9ecf0', strokeDasharray: '0', vertical: false }
const AXIS = { stroke: '#e9ecf0', tickLine: false, axisLine: false, tick: { fill: '#667085', fontSize: 11 } }
const HOVER = { fill: '#f1f3f6' }
const STILL = { isAnimationActive: false }
const TOOLTIP = {
  background: SURFACE, border: '1px solid #e4e7ec', borderRadius: 8, padding: '8px 12px', fontSize: 12,
  color: '#344054', boxShadow: '0 4px 14px rgba(16,24,40,.06)',
}

function Legend({ series }) {
  if (series.length < 2) return null
  return (
    <div className="body" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', padding: '0 20px 8px' }}>
      {series.map(s => (
        <span key={s.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <i style={{ width: 10, height: 10, borderRadius: 3, background: s.color, display: 'inline-block' }} />
          {s.label}
        </span>
      ))}
    </div>
  )
}

function TooltipBox({ active, payload, label, format = fmtInt, extra }) {
  if (!active || !payload?.length) return null
  return (
    <div style={TOOLTIP}>
      {label !== undefined && <div style={{ fontWeight: 650, color: '#101828' }}>{label}</div>}
      {payload.map(p => (
        <div key={p.dataKey ?? p.name}>
          {`${p.name}: ${format(p.value)}`}
        </div>
      ))}
      {extra && <div style={{ color: '#667085' }}>{extra(payload[0].payload)}</div>}
    </div>
  )
}

export function SimpleTable({ head, rows }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>{head.map(h => <th key={h} className="table-header" style={{ textAlign: 'left', padding: 6 }}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.join('|')}>
            {head.map((h, i) => <td key={h} style={{ padding: 6, borderTop: '1px solid var(--line)' }}>{r[i]}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** A chart card with a Table/Chart toggle. */
export function ChartCard({ title, subtitle, series = [], height = 240, empty, table, footer, children }) {
  const [asTable, setAsTable] = useState(false)
  return (
    <Card
      title={title}
      subtitle={subtitle}
      action={table && !empty && (
        <button type="button" onClick={() => setAsTable(t => !t)} className="body" style={{ border: '1px solid var(--line)', background: 'var(--card)', borderRadius: 'var(--radius-md)', padding: '2px 10px', cursor: 'pointer' }}>
          {asTable ? 'Chart' : 'Table'}
        </button>
      )}
    >
      {empty
        ? <Empty>{empty}</Empty>
        : asTable
          ? <div style={{ padding: '0 20px 16px', maxHeight: 420, overflowY: 'auto' }}>{table}</div>
          : (
              <>
                <Legend series={series} />
                <div style={{ height, minWidth: 0, padding: '0 16px 12px 4px' }}>
                  <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
                </div>
              </>
            )}
      {footer && !empty && <p className="body muted" style={{ margin: 0, padding: '0 20px 14px' }}>{footer}</p>}
    </Card>
  )
}

const short = (s, n = 26) => (s && s.length > n ? `${s.slice(0, n - 1)}…` : s)

/** Average project progress per year (one line). Points carry n_projects for the tooltip and table. */
export function ProgressByYearChart({ title = 'Project progress by year', data, subtitle }) {
  const points = data?.points || []
  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      height={250}
      empty={points.length ? null : 'Historical project progress data not available.'}
      table={<SimpleTable head={['Year', 'Average progress', 'Units-weighted', 'Projects reporting']} rows={points.map(p => [p.year, fmtPct(p.avg_progress), fmtPct(p.weighted_progress), fmtInt(p.n_projects)])} />}
      footer={data?.method && `${data.method}${data.excluded_future_points ? ` (${fmtInt(data.excluded_future_points)} excluded)` : ''}`}
    >
      <LineChart data={points} margin={{ top: 16, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="year" {...AXIS} />
        <YAxis width={44} domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={v => `${v}%`} {...AXIS} />
        <Tooltip content={<TooltipBox format={fmtPct} extra={p => `${fmtInt(p.n_projects)} projects reporting`} />} />
        <Line type="monotone" dataKey="avg_progress" name="Average progress" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 4, strokeWidth: 2, stroke: SURFACE }} label={{ position: 'top', fontSize: 11, fill: '#344054', formatter: v => `${Math.round(v)}%` }} {...STILL} />
      </LineChart>
    </ChartCard>
  )
}

/** Horizontal bars, one series. rows: [{ label, value }]. */
export function HBarChart({ title, subtitle, rows, label, format = fmtInt, empty, domain, color = SERIES[0], footer, tableRows }) {
  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      height={Math.max(140, rows.length * 28 + 32)}
      empty={rows.length ? null : empty}
      footer={footer}
      table={<SimpleTable head={['', label]} rows={(tableRows || rows).map(r => [r.label, format(r.value)])} />}
    >
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 40, left: 4, bottom: 0 }}>
        <CartesianGrid {...GRID} horizontal={false} vertical />
        <XAxis type="number" allowDecimals={false} tickFormatter={format} domain={domain} {...AXIS} />
        <YAxis type="category" dataKey="label" width={180} tickFormatter={v => short(v)} {...AXIS} />
        <Tooltip cursor={HOVER} content={<TooltipBox format={format} />} />
        <Bar dataKey="value" name={label} fill={color} maxBarSize={22} radius={[0, 4, 4, 0]} label={{ position: 'right', fontSize: 11, fill: '#344054', formatter: format }} {...STILL} />
      </BarChart>
    </ChartCard>
  )
}

/** Vertical columns by year / band; stacked when there are several series; `ordinal` uses a light→dark ramp. */
export function ColumnChart({ title, subtitle, rows, xKey, series, empty, ordinal = false, height = 230, footer }) {
  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      series={series}
      height={height}
      empty={rows.length ? null : empty}
      footer={footer}
      table={<SimpleTable head={[xKey === 'year' ? 'Year' : '', ...series.map(s => s.label)]} rows={rows.map(r => [r[xKey], ...series.map(s => fmtInt(r[s.key]))])} />}
    >
      <BarChart data={rows} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey={xKey} {...AXIS} />
        <YAxis width={44} allowDecimals={false} tickFormatter={fmtInt} {...AXIS} />
        <Tooltip cursor={HOVER} content={<TooltipBox />} />
        {series.map((s, i) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} stackId="c" fill={s.color} maxBarSize={40} radius={i === series.length - 1 ? [4, 4, 0, 0] : 0} stroke={SURFACE} strokeWidth={series.length > 1 ? 2 : 0} {...STILL}>
            {ordinal && rows.map((r, j) => <Cell key={r[xKey]} fill={RAMP[Math.min(j, RAMP.length - 1)]} />)}
          </Bar>
        ))}
      </BarChart>
    </ChartCard>
  )
}

/** Single-series line over years (counts). */
export function YearLineChart({ title, subtitle, rows, yKey, label, empty, footer }) {
  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      height={210}
      empty={rows.length ? null : empty}
      footer={footer}
      table={<SimpleTable head={['Year', label]} rows={rows.map(r => [r.year, fmtInt(r[yKey])])} />}
    >
      <LineChart data={rows} margin={{ top: 16, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="year" {...AXIS} />
        <YAxis width={40} allowDecimals={false} {...AXIS} />
        <Tooltip content={<TooltipBox />} />
        <Line type="stepAfter" dataKey={yKey} name={label} stroke={SERIES[0]} strokeWidth={2} dot={{ r: 3 }} {...STILL} />
      </LineChart>
    </ChartCard>
  )
}

/** One project's QPR progress history (as published, quarter by quarter). */
export function ProgressHistoryChart({ progress }) {
  const rows = progress.filter(p => p.progress_pct != null).map(p => ({ label: p.quarter_label, progress: Math.min(p.progress_pct, 100) }))
  return (
    <ChartCard
      title="Construction progress history"
      subtitle="Physical progress in each quarterly progress report (QPR), as published"
      height={240}
      empty={rows.length ? null : 'No quarterly progress reports have been filed for this project yet.'}
      table={<SimpleTable head={['Quarter', 'Progress']} rows={rows.map(r => [r.label, fmtPct(r.progress)])} />}
    >
      <LineChart data={rows} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="label" minTickGap={24} {...AXIS} />
        <YAxis width={44} domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={v => `${v}%`} {...AXIS} />
        <Tooltip content={<TooltipBox format={fmtPct} />} />
        <Line type="monotone" dataKey="progress" name="Progress" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 3 }} {...STILL} />
      </LineChart>
    </ChartCard>
  )
}

const STATUS_STYLE = {
  Completed: '#0ca30c', Ongoing: '#2a78d6', Delayed: '#d03b3b', Pending: '#b9b8b0',
}

/** Project status as one compact proportional bar with labelled counts (no donut). */
export function StatusBar({ counts, note }) {
  const items = ['Completed', 'Ongoing', 'Delayed', 'Pending'].map(s => ({ s, n: counts?.[s.toLowerCase()] ?? 0 }))
  const total = items.reduce((a, i) => a + i.n, 0)
  return (
    <div style={{ padding: '0 20px 16px' }}>
      <div role="img" aria-label={items.map(i => `${i.s} ${i.n}`).join(', ')} style={{ display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden', background: 'var(--sunken)', gap: 2 }}>
        {total > 0 && items.filter(i => i.n).map(i => <div key={i.s} title={`${i.s}: ${i.n}`} style={{ flex: i.n, background: STATUS_STYLE[i.s] }} />)}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 20px', marginTop: 10 }}>
        {items.map(i => (
          <span key={i.s} className="body">
            <i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: STATUS_STYLE[i.s], marginRight: 6 }} />
            {`${i.s} `}
            <b style={{ color: 'var(--ink)' }}>{fmtInt(i.n)}</b>
            <span className="muted">{` ${fmtPct(total ? (100 * i.n) / total : null, 0)}`}</span>
          </span>
        ))}
      </div>
      {note && <p className="body muted" style={{ margin: '8px 0 0' }}>{note}</p>}
    </div>
  )
}
