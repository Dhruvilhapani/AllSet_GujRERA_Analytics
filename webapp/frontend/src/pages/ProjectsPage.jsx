/**
 * Projects — every GujRERA project in the dataset, searchable and filterable by city, category, status,
 * developer and locality. Filtering, sorting and paging run on the server over real values.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { Badge, Card, DataTable, DatasetStatus, FilterSelect, LoadError, Page, PageHeader, Pager, SearchInput, Segmented, SkeletonRows, StatusBadge } from '../components/ui.jsx'
import { listProjects } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { CATEGORY_LABEL, fmtDate, fmtInt, fmtPct } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

const ALL = 10000 // the API returns every project in one page up to this size
const PAGE_SIZES = [{ value: '25', label: '25 per page' }, { value: '50', label: '50 per page' }, { value: '100', label: '100 per page' },
  { value: '250', label: '250 per page' }, { value: '500', label: '500 per page' }, { value: String(ALL), label: 'All projects (one page)' }]
const NO_LIST = { projects: [], total: 0, page: 1, page_size: 25, status_counts: {} }
const EMPTY_FILTERS = { city: '', category: '', status: '', locality: '' }
const STATUSES = ['Completed', 'Ongoing', 'Delayed', 'Pending', 'Not crawled']
const UNIT_BADGE = {
  complete: ['Units listed', 'var(--accent-soft)', 'var(--accent)'],
  partial: ['Units incomplete', 'var(--amber-soft)', 'var(--amber-ink)'],
  not_published: ['No unit list published', 'var(--sunken)', 'var(--ink-3)'],
  error: ['Unit fetch failed', 'var(--amber-soft)', 'var(--amber-ink)'],
  not_fetched: ['Units not collected', 'var(--sunken)', 'var(--ink-3)'],
}

// Column key -> server sort key (queries.PROJECT_SORT_COLUMNS)
const SORT_KEY = { name: 'name', locality: 'locality', registration_date: 'registration', current_completion_date: 'completion', total_units: 'units', sales_pct: 'sales_pct', progress_pct: 'progress' }
const COLUMN_FOR_SORT = Object.fromEntries(Object.entries(SORT_KEY).map(([c, s]) => [s, c]))

export function UnitDataBadge({ state }) {
  const [label, fill, ink] = UNIT_BADGE[state] || ['Units n/a', 'var(--sunken)', 'var(--ink-3)']
  return <Badge fill={fill} ink={ink}>{label}</Badge>
}

export default function ProjectsPage() {
  const navigate = useNavigate()
  const { meta: m, version, error: metaError } = useDataVersion()
  const [params] = useSearchParams()
  const [search, setSearch] = useState(params.get('q') || '')
  const [q, setQ] = useState((params.get('q') || '').trim())
  const [devSearch, setDevSearch] = useState('')
  const [developer, setDeveloper] = useState('')
  const [filters, setFilters] = useState(() => ({
    ...EMPTY_FILTERS, city: params.get('city') || '', category: params.get('category') || '', status: params.get('status') || '',
    locality: params.get('locality') || '',
  }))
  const [sort, setSort] = useState({ by: 'registration', dir: 'desc' })
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)

  useEffect(() => { // a new URL (e.g. the global search's "see all", a locality link) replaces the filters
    setSearch(params.get('q') || '')
    setFilters({
      ...EMPTY_FILTERS, city: params.get('city') || '', category: params.get('category') || '', status: params.get('status') || '',
      locality: params.get('locality') || '',
    })
    setPage(1)
  }, [params])

  useEffect(() => { // debounce typing so each keystroke is not a request
    const t = setTimeout(() => {
      setQ(search.trim())
      setDeveloper(devSearch.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [search, devSearch])

  const list = useFetch(
    listProjects,
    version ? { q, developer, ...filters, sort_by: sort.by, sort_dir: sort.dir, page, page_size: pageSize, v: version } : null,
    NO_LIST,
  )
  const data = list.data || NO_LIST
  const pages = Math.max(1, Math.ceil(data.total / pageSize))
  const set = patch => {
    setFilters(f => ({ ...f, ...patch }))
    setPage(1)
  }
  const cities = m?.cities || []
  const categories = m?.categories || []
  const localities = filters.city ? (m?.localities_by_city?.[filters.city] || []) : (m?.localities || [])
  const onSort = (key) => {
    const by = SORT_KEY[key]
    setSort(s => ({ by, dir: s.by === by && s.dir === 'desc' ? 'asc' : 'desc' }))
    setPage(1)
  }

  const columns = [
    {
      key: 'name',
      label: 'Project',
      sortable: true,
      render: r => (
        <div style={{ whiteSpace: 'normal', minWidth: 190 }}>
          <div style={{ color: 'var(--ink)', fontWeight: 550 }}>{r.name}</div>
          <div className="body muted">{`${CATEGORY_LABEL[r.category] || r.project_type || ''} · ${r.rera_number || 'RERA no. n/a'}`}</div>
        </div>
      ),
    },
    {
      key: 'locality',
      label: 'Location',
      sortable: true,
      render: r => <span style={{ whiteSpace: 'normal' }}>{[r.locality, r.city].filter(Boolean).join(', ') || 'N/A'}</span>,
    },
    { key: 'developer_name', label: 'Developer', render: r => <span style={{ whiteSpace: 'normal' }}>{r.developer_name || 'N/A'}</span> },
    { key: 'status', label: 'Status', render: r => <StatusBadge status={r.status} /> },
    { key: 'registration_date', label: 'Registered', sortable: true, render: r => fmtDate(r.registration_date) },
    { key: 'total_units', label: 'Units', align: 'right', sortable: true, render: r => fmtInt(r.total_units) },
    { key: 'sales_pct', label: 'Sales %', align: 'right', sortable: true, render: r => fmtPct(r.sales_pct, 0) },
    { key: 'progress_pct', label: 'Progress', align: 'right', sortable: true, render: r => fmtPct(r.progress_pct, 0) },
    { key: 'unit_data_state', label: 'Unit data', render: r => <UnitDataBadge state={r.unit_data_state} /> },
  ]

  return (
    <Page>
      <PageHeader title="Projects" subtitle={`${m?.scope || 'Projects registered with GujRERA'}${cities.length ? ` · ${cities.join(', ')}` : ''}`} />
      {metaError && <LoadError>{metaError}</LoadError>}
      <DatasetStatus meta={m} />
      <Card title="All projects" subtitle={list.loading ? 'Loading projects…' : `${fmtInt(data.total)} projects match`}>
        <div style={{ padding: '0 20px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 14 }}>
            <SearchInput id="project-search" value={search} onChange={setSearch} placeholder="Search project name or RERA no..." label="Search project" />
            <SearchInput id="project-developer" value={devSearch} onChange={setDevSearch} placeholder="Developer..." label="Search developer" />
            {cities.length > 1 && (
              <Segmented label="City" value={filters.city} onChange={v => set({ city: v, locality: '' })} options={[{ value: '', label: 'All cities' }, ...cities.map(c => ({ value: c, label: c }))]} />
            )}
            {categories.length > 1 && (
              <Segmented label="Project category" value={filters.category} onChange={v => set({ category: v })} options={[{ value: '', label: 'All types' }, ...categories.map(c => ({ value: c, label: CATEGORY_LABEL[c] || c }))]} />
            )}
            <FilterSelect id="f-status" ariaLabel="Project status" value={filters.status} onChange={v => set({ status: v })} options={[{ value: '', label: 'Any status' }, ...STATUSES.map(s => ({ value: s, label: s }))]} />
            <FilterSelect id="f-locality" ariaLabel="Location" value={filters.locality} onChange={v => set({ locality: v })} options={[{ value: '', label: 'All locations' }, ...localities.map(l => ({ value: l, label: l }))]} />
            <FilterSelect
              id="f-page-size"
              ariaLabel="Projects per page"
              value={String(pageSize)}
              onChange={(v) => {
                setPageSize(Number(v))
                setPage(1)
              }}
              options={PAGE_SIZES}
            />
          </div>
          {list.error && <LoadError>{list.error}</LoadError>}
        </div>
        {(list.loading || !m) && !data.projects.length && !metaError
          ? <SkeletonRows rows={10} label="Loading projects…" />
          : (
              <div style={{ opacity: list.loading ? 0.6 : 1, transition: 'opacity .15s' }}>
                <DataTable
                  columns={columns}
                  rows={data.projects}
                  rowKey={r => r.project_id}
                  sort={{ by: COLUMN_FOR_SORT[sort.by] || sort.by, dir: sort.dir }}
                  onSort={onSort}
                  onRowClick={r => navigate(`/projects/${r.project_id}`)}
                  rowLabel={r => `Open project ${r.name}`}
                  empty="No projects match these filters."
                />
              </div>
            )}
        {pageSize >= ALL
          ? <p className="body muted" style={{ padding: '12px 20px', margin: 0 }}>{`Showing all ${fmtInt(data.projects.length)} of ${fmtInt(data.total)} matching projects.`}</p>
          : <Pager page={data.page} pages={pages} onPage={setPage} total={data.total} pageSize={pageSize} noun="projects" />}
      </Card>
      <p className="body muted" style={{ marginTop: 12 }}>
        Units, Sales % and Progress are as last reported to GujRERA (quarterly reports); N/A means the source does not publish
        the figure or the project has not been crawled yet, never zero. &quot;Unit data&quot; says whether the individual
        unit list (Unit Wise Booking Status) has been captured for the project.
      </p>
    </Page>
  )
}
