/**
 * Developer-section components. They render the sections computed by the server analytics layer
 * (webapp/server/analytics.py); no business metric is calculated here. Unknown values read "N/A", never 0.
 */

import { useMemo, useState } from 'react'

import { listDevelopers } from '../lib/api.js'
import { CATEGORY_LABEL, fmtDate, fmtDays, fmtInt, fmtPct, fmtRupees, isKnown } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

import { HBarChart, StatusBar, YearLineChart, ColumnChart, SERIES } from './charts.jsx'
import { Badge, Card, DataTable, Empty, FilterSelect, LoadError, Segmented, SkeletonRows, SmallButton, StatusBadge } from './ui.jsx'

/* ------------------------------------------------------------------ list page */

const CATEGORY_OPTIONS = [{ value: '', label: 'All types' }, { value: 'residential', label: 'Residential' }, { value: 'mixed', label: 'Mixed-use' }]
const MIN_PROJECTS = [{ value: '', label: 'Any project count' }, { value: '2', label: '2+ projects' }, { value: '5', label: '5+ projects' }, { value: '10', label: '10+ projects' }]
const MIN_SALES = [{ value: '', label: 'Any sales %' }, { value: '50', label: 'Sales 50%+' }, { value: '75', label: 'Sales 75%+' }, { value: '90', label: 'Sales 90%+' }]

function Toggle({ id, label, checked, onChange }) {
  return (
    <label htmlFor={id} className="body" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
      <input id={id} type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

/** Every option maps to a server-side filter over real values; unknown figures never match. */
export function DeveloperFilters({ filters, onChange, localities, search, onSearch }) {
  const set = patch => onChange({ ...filters, ...patch })
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 14 }}>
      <input
        id="developer-search"
        type="search"
        value={search}
        onChange={e => onSearch(e.target.value)}
        placeholder="Search developer..."
        aria-label="Search developer"
        style={{ flex: '1 1 240px', maxWidth: 340, padding: '7px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--line-strong)', background: 'var(--card)', color: 'var(--ink)', fontSize: 13 }}
      />
      <Segmented options={CATEGORY_OPTIONS} value={filters.category} onChange={v => set({ category: v })} label="Project type" />
      <FilterSelect id="f-locality" ariaLabel="Location" value={filters.locality} onChange={v => set({ locality: v })} options={[{ value: '', label: 'All locations' }, ...localities.map(l => ({ value: l, label: l }))]} />
      <FilterSelect id="f-min-projects" ariaLabel="Minimum projects" value={filters.min_projects} onChange={v => set({ min_projects: v })} options={MIN_PROJECTS} />
      <FilterSelect id="f-min-sales" ariaLabel="Minimum sales percentage" value={filters.min_sales_pct} onChange={v => set({ min_sales_pct: v })} options={MIN_SALES} />
      <Toggle id="f-delayed" label="Has delayed projects" checked={filters.has_delayed === '1'} onChange={c => set({ has_delayed: c ? '1' : '' })} />
      <Toggle id="f-complaints" label="Has complaints" checked={filters.has_complaints === '1'} onChange={c => set({ has_complaints: c ? '1' : '' })} />
    </div>
  )
}

// Column key -> server sort key (queries.SORT_COLUMNS).
const SORT_KEY = { name: 'name', projects_total: 'projects', completed: 'completed', ongoing: 'ongoing', delayed: 'delayed', pending: 'pending', avg_progress_pct: 'progress', sales_pct: 'sales_pct', complaints_total: 'complaints', total_extensions: 'extensions', recent_projects: 'growth' }
const COLUMN_FOR_SORT = Object.fromEntries(Object.entries(SORT_KEY).map(([c, s]) => [s, c]))

export function DeveloperTable({ rows, sort, onSort, onOpen, selected, onToggleSelect, maxSelect }) {
  const num = (key, label, fmt = fmtInt) => ({ key, label, align: 'right', sortable: true, render: r => fmt(r[key]) })
  const columns = [
    {
      key: 'select',
      label: '',
      render: (r) => {
        const checked = selected.includes(r.developer_id)
        return (
          <input
            type="checkbox"
            aria-label={`Compare ${r.name}`}
            checked={checked}
            disabled={!checked && selected.length >= maxSelect}
            onClick={e => e.stopPropagation()}
            onChange={() => onToggleSelect(r.developer_id)}
          />
        )
      },
    },
    {
      key: 'name',
      label: 'Developer',
      sortable: true,
      render: r => (
        <div style={{ whiteSpace: 'normal', minWidth: 180 }}>
          <div style={{ color: 'var(--ink)', fontWeight: 550 }}>{r.name}</div>
          {r.projects_analyzed < r.projects_total && (
            <div className="body" style={{ color: 'var(--amber-ink)' }}>{`${r.projects_analyzed} of ${r.projects_total} analysed`}</div>
          )}
        </div>
      ),
    },
    num('projects_total', 'Projects'),
    num('completed', 'Completed'),
    num('ongoing', 'Ongoing'),
    num('delayed', 'Delayed'),
    num('avg_progress_pct', 'Avg. progress', v => fmtPct(v, 0)),
    num('sales_pct', 'Sales %', v => fmtPct(v, 0)),
    num('total_extensions', 'Extensions'),
    num('complaints_total', 'Complaints'),
  ]
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={r => r.developer_id}
      sort={{ by: COLUMN_FOR_SORT[sort.by] || sort.by, dir: sort.dir }}
      onSort={key => onSort(SORT_KEY[key] || key)}
      onRowClick={r => onOpen(r.developer_id)}
      rowLabel={r => `Open ${r.name}`}
      empty="No developers found."
    />
  )
}

const COMPARE_ROWS = [
  ['Projects', d => fmtInt(d.projects_total)],
  ['Projects analysed', d => fmtInt(d.projects_analyzed)],
  ['Completed', d => fmtInt(d.completed)],
  ['Ongoing', d => fmtInt(d.ongoing)],
  ['Delayed', d => fmtInt(d.delayed)],
  ['Completion rate', d => fmtPct(d.completion_rate_pct)],
  ['Avg. project progress', d => fmtPct(d.avg_progress_pct)],
  ['Sales %', d => fmtPct(d.sales_pct)],
  ['Projects with extensions', d => fmtInt(d.extended_projects)],
  ['Complaints', d => fmtInt(d.complaints_total)],
  ['Projects registered in last 24 months', d => fmtInt(d.recent_projects)],
]

export function DeveloperComparison({ ids, onClose }) {
  const { data, loading, error } = useFetch(listDevelopers, ids.length >= 2 ? { ids: ids.join(','), page_size: 4, sort_by: 'name', sort_dir: 'asc' } : null)
  const devs = data?.developers || []
  return (
    <Card title="Developer comparison" subtitle="Same metric definitions for every developer. N/A = not published or not crawled yet." action={<SmallButton onClick={onClose}>Close</SmallButton>} style={{ marginBottom: 16 }}>
      {error && <div style={{ padding: '0 20px' }}><LoadError>{error}</LoadError></div>}
      {loading && !devs.length
        ? <SkeletonRows rows={5} label="Loading comparison…" />
        : (
            <div style={{ padding: '0 20px 20px' }}>
              <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%' }}>
                <thead>
                  <tr>
                    <th className="table-header" style={{ textAlign: 'left', padding: 8 }}>Metric</th>
                    {devs.map(d => <th key={d.developer_id} className="table-header" style={{ textAlign: 'right', padding: 8, color: 'var(--ink)', whiteSpace: 'normal' }}>{d.name}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {COMPARE_ROWS.map(([label, fn]) => (
                    <tr key={label}>
                      <td style={{ padding: 8, borderTop: '1px solid var(--line)', color: 'var(--ink-3)' }}>{label}</td>
                      {devs.map(d => <td key={d.developer_id} style={{ padding: 8, borderTop: '1px solid var(--line)', textAlign: 'right' }}>{fn(d)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
    </Card>
  )
}

/* ------------------------------------------------------------------ developer page sections */

export function SectionTitle({ children, note }) {
  return (
    <div style={{ margin: '28px 0 12px' }}>
      <h2 className="section-title">{children}</h2>
      {note && <p className="body muted" style={{ margin: '2px 0 0' }}>{note}</p>}
    </div>
  )
}

/** Project | Location | Status | Units | Sold | Remaining | Sales % | Progress — sortable, names wrap. */
export function ProjectsTable({ projects, onOpen }) {
  const [sort, setSort] = useState({ by: 'registration_date', dir: 'desc' })
  const rows = useMemo(() => {
    const out = [...projects]
    out.sort((a, b) => {
      const x = a[sort.by]
      const y = b[sort.by]
      if (!isKnown(x)) return 1
      if (!isKnown(y)) return -1
      const c = typeof x === 'number' ? x - y : String(x).localeCompare(String(y))
      return sort.dir === 'asc' ? c : -c
    })
    return out
  }, [projects, sort])
  const col = (key, label, render, align) => ({ key, label, sortable: true, align, render })
  return (
    <Card title={`Projects (${projects.length})`} subtitle="Inventory as reported to GujRERA. Click a project for its full record.">
      <DataTable
        columns={[
          col('name', 'Project', r => (
            <div style={{ whiteSpace: 'normal', minWidth: 160 }}>
              <div style={{ color: 'var(--ink)', fontWeight: 550 }}>{r.name}</div>
              <div className="body muted">{CATEGORY_LABEL[r.category] || ''}</div>
            </div>
          )),
          col('location', 'Location', r => <span style={{ whiteSpace: 'normal' }}>{r.location || 'N/A'}</span>),
          col('status', 'Status', r => <StatusBadge status={r.status} />),
          col('units', 'Units', r => fmtInt(r.units), 'right'),
          col('sold', 'Sold', r => fmtInt(r.sold), 'right'),
          col('remaining', 'Remaining', r => fmtInt(r.remaining), 'right'),
          col('sales_pct', 'Sales %', r => fmtPct(r.sales_pct, 0), 'right'),
          col('progress_pct', 'Progress', r => fmtPct(r.progress_pct, 0), 'right'),
        ]}
        rows={rows}
        rowKey={r => r.project_id}
        sort={sort}
        onSort={by => setSort(s => ({ by, dir: s.by === by && s.dir === 'desc' ? 'asc' : 'desc' }))}
        onRowClick={r => onOpen(r.project_id)}
        rowLabel={r => `Open project ${r.name}`}
        empty="No projects available for this developer."
      />
    </Card>
  )
}

const MAX_SALES_BARS = 20

/** One sales visual: Sales % by project; long portfolios show the 10 highest and 10 lowest (table has all). */
export function SalesSection({ sales, highlights, onOpen }) {
  const all = sales.projects.map(p => ({ label: p.name, value: p.sales_pct, id: p.project_id }))
  const rows = all.length > MAX_SALES_BARS ? [...all.slice(0, 10), ...all.slice(-10)] : all
  return (
    <div className="split split-even">
      <HBarChart
        title="Sales % by project"
        subtitle={`Share of units booked${all.length > MAX_SALES_BARS ? ' · 10 highest and 10 lowest shown, all in table' : ''}`}
        rows={rows}
        tableRows={all}
        label="Sales %"
        format={v => fmtPct(v, 0)}
        domain={[0, 100]}
        empty="Sales data is not available yet for this developer."
        footer={`Median ${fmtPct(sales.median_sales_pct, 0)} across ${fmtInt(sales.projects_with_data)} projects with booking data${sales.projects_without_data ? `; ${fmtInt(sales.projects_without_data)} without` : ''}.`}
      />
      <Card title="Project highlights" subtitle="Each line states the metric it ranks by.">
        {highlights.length === 0
          ? <Empty>Not enough projects with comparable data.</Empty>
          : (
              <div style={{ padding: '0 20px 16px' }}>
                {highlights.map(h => (
                  <div key={h.label} style={{ borderTop: '1px solid var(--line)', padding: '10px 0', display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
                    <div style={{ minWidth: 0 }}>
                      <div className="body-strong">{h.label}</div>
                      <button type="button" onClick={() => onOpen(h.project_id)} style={{ all: 'unset', cursor: 'pointer', color: 'var(--accent)' }}>{h.name}</button>
                      <div className="body muted">{h.metric}</div>
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--ink)' }}>{fmtPct(h.value, 0)}</div>
                  </div>
                ))}
              </div>
            )}
      </Card>
    </div>
  )
}

/** Construction status, extensions and delays — kept separate: an extension is an approval, not a delay. */
export function ConstructionSection({ construction, extensions, delays, onOpen }) {
  return (
    <>
      <Card title="Construction status" subtitle="Analysed projects. Completed = completion filing; Delayed = past the latest approved completion date; Pending = no progress report yet.">
        <StatusBar
          counts={construction}
          note={[
            construction.stalled ? `${fmtInt(construction.stalled)} ongoing project(s) show no recent progress (stalled).` : null,
            construction.not_crawled ? `${fmtInt(construction.not_crawled)} project(s) not crawled yet.` : null,
          ].filter(Boolean).join(' ') || null}
        />
      </Card>
      <div className="split split-even" style={{ marginTop: 16 }}>
        <Card
          title={`Extensions · ${fmtInt(extensions.projects_with_extensions)} project(s)`}
          subtitle={`${fmtInt(extensions.extension_events)} approved extension(s)${extensions.avg_extension_months != null ? ` · average ${extensions.avg_extension_months} months, longest ${extensions.max_extension_months} months` : ''}. GujRERA does not publish extension reasons. An extension is an approved revision, not a finding.`}
        >
          <DataTable
            columns={[
              { key: 'name', label: 'Project', render: r => <span style={{ whiteSpace: 'normal', color: 'var(--ink)' }}>{r.name}</span> },
              { key: 'original_completion_date', label: 'Original', render: r => fmtDate(r.original_completion_date) },
              { key: 'revised_completion_date', label: 'Revised', render: r => fmtDate(r.revised_completion_date) },
              { key: 'extension_months', label: 'Duration', align: 'right', render: r => (r.extension_months == null ? 'N/A' : `${r.extension_months} mo`) },
              { key: 'extensions', label: 'Count', align: 'right', render: r => fmtInt(r.extensions) },
              { key: 'status', label: 'Status', render: r => <StatusBadge status={r.status} /> },
            ]}
            rows={extensions.projects}
            rowKey={r => r.project_id}
            onRowClick={r => onOpen(r.project_id)}
            rowLabel={r => `Open project ${r.name}`}
            empty="No extensions recorded for the analysed projects."
          />
        </Card>
        <Card title={`Delayed projects · ${fmtInt(delays.length)}`} subtitle="Past the latest approved completion date with no completion filing. Missing dates are never treated as a delay.">
          <DataTable
            columns={[
              { key: 'name', label: 'Project', render: r => <span style={{ whiteSpace: 'normal', color: 'var(--ink)' }}>{r.name}</span> },
              { key: 'approved_completion_date', label: 'Approved completion', render: r => fmtDate(r.approved_completion_date) },
              { key: 'days_past_approved_date', label: 'Past by', align: 'right', render: r => fmtDays(r.days_past_approved_date) },
              { key: 'progress_pct', label: 'Progress', align: 'right', render: r => fmtPct(r.progress_pct, 0) },
            ]}
            rows={delays}
            rowKey={r => r.project_id}
            onRowClick={r => onOpen(r.project_id)}
            rowLabel={r => `Open project ${r.name}`}
            empty="No delayed projects."
          />
        </Card>
      </div>
    </>
  )
}

const RECORD_LABEL = { complaint: 'Complaint', suo_motu: 'Suo-motu proceeding', challan: 'Penalty challan', appeal_name_matched: 'Appeal (name match)' }
const CATEGORY_TEXT = {
  rera_complaint: 'Complaint to Authority (Form A)', adjudication_complaint: 'Adjudication (Form B)', review: 'Review',
  ratification: 'Ratification', conciliation: 'Conciliation', complaint_unspecified_form: 'Complaint',
  suo_motu_proceeding: 'Suo-motu', penalty_challan: 'Challan', appellate_tribunal: 'Appellate Tribunal', unknown: 'Other',
}

/** Factual GujRERA records only — what was filed, ordered and levied. No conclusions. */
export function LegalTable({ records, showProject = true }) {
  return (
    <DataTable
      columns={[
        ...(showProject ? [{ key: 'project_name', label: 'Project', render: r => <span style={{ whiteSpace: 'normal', color: 'var(--ink)' }}>{r.project_name}</span> }] : []),
        { key: 'record_type', label: 'Record', render: r => RECORD_LABEL[r.record_type] || r.record_type },
        { key: 'category', label: 'Category', render: r => <span style={{ whiteSpace: 'normal' }}>{r.subtype && r.record_type !== 'complaint' ? r.subtype : (CATEGORY_TEXT[r.category] || r.category || 'N/A')}</span> },
        { key: 'filed_date', label: 'Filed', render: r => fmtDate(r.filed_date) },
        { key: 'status', label: 'Status', render: r => (r.status === 'order_published' ? 'Order published' : r.status || 'N/A') },
        { key: 'penalty_amount', label: 'Amount', align: 'right', render: r => fmtRupees(r.penalty_amount) },
        { key: 'order_url', label: 'Order', render: r => (r.order_url ? <a href={r.order_url} target="_blank" rel="noreferrer">PDF</a> : 'N/A') },
      ]}
      rows={records}
      rowKey={r => `${r.record_type}-${r.reference}-${r.project_id ?? ''}`}
      empty="No complaints, proceedings or penalties recorded."
    />
  )
}

const EVENT_TONE = { registration: 'var(--ink-4)', extension: 'var(--amber)', complaint_filed: 'var(--danger)', suo_motu: 'var(--violet)', challan: 'var(--danger)', order: 'var(--ink-2)', completion: 'var(--good)' }

export function Timeline({ events, showProject = true, limit = 30 }) {
  const [all, setAll] = useState(false)
  if (!events.length) return <Empty>No dated events recorded yet.</Empty>
  const sorted = [...events].sort((a, b) => String(b.event_date).localeCompare(String(a.event_date)))
  const shown = all ? sorted : sorted.slice(0, limit)
  return (
    <div style={{ padding: '0 20px 16px' }}>
      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {shown.map(e => (
          <li key={`${e.event_date}-${e.event_type}-${e.reference ?? ''}-${e.project_id ?? ''}-${e.title}`} style={{ display: 'grid', gridTemplateColumns: '96px 14px 1fr', gap: 10, padding: '6px 0', borderTop: '1px solid var(--line)' }}>
            <span className="body muted">{fmtDate(e.event_date)}</span>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 4, marginTop: 6, background: EVENT_TONE[e.event_type] || 'var(--ink-4)' }} />
            <span className="body">
              <b style={{ color: 'var(--ink)' }}>{e.title}</b>
              {showProject && e.project_name ? ` · ${e.project_name}` : ''}
              {e.detail ? ` · ${e.detail}` : ''}
            </span>
          </li>
        ))}
      </ol>
      {sorted.length > limit && (
        <button type="button" onClick={() => setAll(a => !a)} className="body" style={{ marginTop: 8, border: 'none', background: 'none', color: 'var(--accent)', cursor: 'pointer', padding: 0 }}>
          {all ? 'Show fewer' : `Show all ${sorted.length} events`}
        </button>
      )}
    </div>
  )
}

const STATE_LABEL = { open: 'Open', closed: 'Closed', unknown: 'Not published' }
const STATE_TONE = {
  open: { fill: 'var(--amber-soft)', ink: 'var(--amber-ink)' },
  closed: { fill: 'var(--sunken)', ink: 'var(--ink-2)' },
  unknown: { fill: 'var(--sunken)', ink: 'var(--ink-3)' },
}
const STATE_FILTERS = [{ value: '', label: 'All' }, { value: 'open', label: 'Open' }, { value: 'closed', label: 'Closed' }]
const COMPLAINTS_PAGE = 50

function Count({ label, value }) {
  return (
    <div>
      <div className="body muted">{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>{value}</div>
    </div>
  )
}

/** Every complaint filed against any of the developer's projects, in one list (newest first). */
export function ComplaintsSection({ complaints, onOpenProject }) {
  const [state, setState] = useState('')
  const [project, setProject] = useState('')
  const [limit, setLimit] = useState(COMPLAINTS_PAGE)
  const projects = useMemo(
    () => [...new Map(complaints.records.map(r => [String(r.project_id), r.project_name])).entries()]
      .sort((x, y) => String(x[1]).localeCompare(String(y[1]))),
    [complaints.records],
  )
  const rows = complaints.records.filter(r => (!state || r.state === state) && (!project || String(r.project_id) === project))
  return (
    <Card
      title={`All complaints · ${fmtInt(complaints.total)}`}
      subtitle="Every complaint filed with GujRERA against this developer's projects, newest first. A complaint is a filing, not proof of wrongdoing."
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, padding: '0 20px 12px' }}>
        <Count label="Total complaints" value={fmtInt(complaints.total)} />
        <Count label="Open" value={fmtInt(complaints.open)} />
        <Count label="Closed" value={fmtInt(complaints.closed)} />
        <Count label="Projects with complaints" value={fmtInt(complaints.projects_with_complaints)} />
      </div>
      {complaints.by_type.length > 0 && (
        <div style={{ padding: '0 20px 12px' }} className="body">
          {complaints.by_type.map(t => `${t.type}: ${fmtInt(t.total)} (${fmtInt(t.open)} open)`).join(' · ')}
        </div>
      )}
      {complaints.total > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', padding: '0 20px 12px' }}>
          <Segmented options={STATE_FILTERS} value={state} onChange={(v) => { setState(v); setLimit(COMPLAINTS_PAGE) }} label="Complaint status" />
          {projects.length > 1 && (
            <FilterSelect
              id="complaint-project"
              ariaLabel="Project"
              value={project}
              onChange={(v) => { setProject(v); setLimit(COMPLAINTS_PAGE) }}
              options={[{ value: '', label: 'All projects' }, ...projects.map(([id, name]) => ({ value: id, label: name }))]}
            />
          )}
          <span className="body muted">{`${fmtInt(rows.length)} shown`}</span>
        </div>
      )}
      <DataTable
        columns={[
          { key: 'filed_date', label: 'Filed', render: r => fmtDate(r.filed_date) },
          { key: 'reference', label: 'Complaint no.', render: r => <span style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{r.reference}</span> },
          {
            key: 'project_name',
            label: 'Project',
            render: r => (
              <button type="button" onClick={() => onOpenProject(r.project_id)} style={{ all: 'unset', cursor: 'pointer', color: 'var(--accent)', whiteSpace: 'normal' }}>
                {r.project_name}
              </button>
            ),
          },
          { key: 'type', label: 'Type', render: r => <span style={{ whiteSpace: 'normal' }}>{r.type}</span> },
          {
            key: 'state',
            label: 'Status',
            render: r => (
              <span title={r.status || ''}>
                <Badge fill={STATE_TONE[r.state].fill} ink={STATE_TONE[r.state].ink}>{STATE_LABEL[r.state]}</Badge>
                <span className="body muted" style={{ marginLeft: 6 }}>{r.status === 'order_published' ? 'order published' : (r.status || '').toLowerCase().replaceAll('_', ' ')}</span>
              </span>
            ),
          },
          { key: 'order_date', label: 'Order date', render: r => fmtDate(r.order_date) },
          { key: 'order_url', label: 'Order', render: r => (r.order_url ? <a href={r.order_url} target="_blank" rel="noreferrer">PDF</a> : 'N/A') },
        ]}
        rows={rows.slice(0, limit)}
        rowKey={r => `${r.reference}-${r.project_id}`}
        empty={complaints.total ? 'No complaints match this filter.' : 'No complaints recorded against this developer\'s projects.'}
      />
      {rows.length > limit && (
        <div style={{ padding: '10px 20px 16px' }}>
          <SmallButton onClick={() => setLimit(l => l + COMPLAINTS_PAGE)}>{`Show more (${fmtInt(rows.length - limit)} left)`}</SmallButton>
        </div>
      )}
    </Card>
  )
}

/** Suo-motu proceedings and penalty challans (Authority actions, separate from complaints) + dated timeline. */
export function RegulatorySection({ regulatory, events }) {
  return (
    <Card
      title="Regulatory actions"
      subtitle="Suo-motu proceedings and penalty challans initiated by GujRERA itself (not complaints)."
      style={{ marginTop: 16 }}
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, padding: '0 20px 12px' }}>
        <Count label="Suo-motu proceedings" value={fmtInt(regulatory.suo_motu)} />
        <Count label="Penalty challans" value={fmtInt(regulatory.challans)} />
        <Count label="Penalties levied" value={fmtRupees(regulatory.penalty_amount)} />
      </div>
      {regulatory.records.length > 0 && <LegalTable records={regulatory.records} />}
      {events.length > 0 && (
        <details style={{ borderTop: '1px solid var(--line)' }}>
          <summary className="body-strong" style={{ cursor: 'pointer', padding: '12px 20px' }}>{`Show full timeline (${events.length} events)`}</summary>
          <Timeline events={events} />
        </details>
      )}
    </Card>
  )
}

export function LocationTable({ locations }) {
  return (
    <Card title="Locations" subtitle="Areas are GujRERA's own locality (moje) field; no boundaries are inferred.">
      <DataTable
        columns={[
          { key: 'area', label: 'Area', render: r => <span style={{ color: 'var(--ink)' }}>{r.area}</span> },
          { key: 'projects', label: 'Projects', align: 'right', render: r => fmtInt(r.projects) },
          { key: 'completed', label: 'Completed', align: 'right', render: r => fmtInt(r.completed) },
          { key: 'avg_progress', label: 'Avg. progress', align: 'right', render: r => fmtPct(r.avg_progress, 0) },
        ]}
        rows={locations}
        rowKey={r => r.area}
        empty="No projects."
      />
    </Card>
  )
}

export function GrowthSection({ growth }) {
  const exp = growth.expansion
  return (
    <div className="split split-even">
      <ColumnChart
        title="Project portfolio growth"
        subtitle="Projects registered per year (GujRERA approval year)"
        rows={growth.registrations}
        xKey="year"
        series={[{ key: 'projects', label: 'Projects', color: SERIES[0] }]}
        empty="No registration history available."
        height={200}
      />
      <YearLineChart
        title="Geographic expansion"
        subtitle="Cumulative number of areas with a registered project"
        rows={exp.points}
        yKey="areas"
        label="Areas"
        empty="No locality history available."
        footer={exp.projects_without_locality ? `${fmtInt(exp.projects_without_locality)} project(s) without a published locality are not counted.` : null}
      />
    </div>
  )
}
