/**
 * Developers — master list. Search, filters, sorting and paging run on the
 * server over real values; while the crawl runs the page says it shows the
 * current (partial) dataset, not all of Ahmedabad.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { DeveloperComparison, DeveloperFilters, DeveloperTable } from '../components/developers.jsx'
import { Card, DatasetStatus, LoadError, Page, PageHeader, SkeletonRows, SmallButton } from '../components/ui.jsx'
import { listDevelopers } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { fmtInt } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

const PAGE_SIZE = 25
const MAX_COMPARE = 4
const EMPTY_FILTERS = { city: '', category: '', locality: '', min_projects: '', min_sales_pct: '', has_delayed: '', has_complaints: '' }
const NO_LIST = { developers: [], total: 0, page: 1, page_size: PAGE_SIZE }

export default function DevelopersPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [search, setSearch] = useState(params.get('q') || '')
  const [q, setQ] = useState((params.get('q') || '').trim())
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [sort, setSort] = useState({ by: 'projects', dir: 'desc' })
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState([])
  const [comparing, setComparing] = useState(false)

  useEffect(() => { // the global search's "see all developers" link sets ?q=
    setSearch(params.get('q') || '')
  }, [params])

  useEffect(() => { // debounce typing so each keystroke is not a request
    const t = setTimeout(() => {
      setQ(search.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [search])

  const { meta: m, version, error: metaError } = useDataVersion()
  const list = useFetch(
    listDevelopers,
    version ? { q, ...filters, sort_by: sort.by, sort_dir: sort.dir, page, page_size: PAGE_SIZE, v: version } : null,
    NO_LIST,
  )
  const data = list.data || NO_LIST
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE))

  const onSort = by => {
    setSort(s => ({ by, dir: s.by === by && s.dir === 'desc' ? 'asc' : 'desc' }))
    setPage(1)
  }
  const onFilters = (next) => {
    setFilters(next)
    setPage(1)
  }
  const toggleSelect = id => setSelected(s => (s.includes(id) ? s.filter(x => x !== id) : [...s, id].slice(0, MAX_COMPARE)))

  return (
    <Page>
      <PageHeader title="Developers" subtitle={`${m?.district || 'GujRERA'} Developer Intelligence · ${m?.scope || 'Projects registered with GujRERA'}`} />
      {metaError && <LoadError>{metaError}</LoadError>}
      <DatasetStatus meta={m} />
      {comparing && selected.length >= 2 && <DeveloperComparison ids={selected} onClose={() => setComparing(false)} />}
      <Card
        title={m?.dataset_complete ? 'All developers' : 'Developers in current dataset'}
        subtitle={list.loading ? 'Loading developers…' : `${fmtInt(data.total)} developers match`}
        action={(
          <SmallButton primary disabled={selected.length < 2} onClick={() => setComparing(true)}>
            {`Compare${selected.length ? ` (${selected.length})` : ''}`}
          </SmallButton>
        )}
      >
        <div style={{ padding: '0 20px' }}>
          <DeveloperFilters
            filters={filters}
            onChange={onFilters}
            localities={filters.city ? (m?.localities_by_city?.[filters.city] || []) : (m?.localities || [])}
            cities={m?.cities || []}
            categories={m?.categories || ['residential', 'mixed']}
            search={search}
            onSearch={setSearch}
          />
          {list.error && <LoadError>{list.error}</LoadError>}
        </div>
        {(list.loading || !m) && !data.developers.length && !metaError
          ? <SkeletonRows rows={10} label="Loading developers…" />
          : (
              <div style={{ opacity: list.loading ? 0.6 : 1, transition: 'opacity .15s' }}>
                <DeveloperTable
                  rows={data.developers}
                  sort={sort}
                  onSort={onSort}
                  onOpen={id => navigate(`/developers/${id}`)}
                  selected={selected}
                  onToggleSelect={toggleSelect}
                  maxSelect={MAX_COMPARE}
                />
              </div>
            )}
        <footer className="body muted" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '12px 20px' }}>
          <span>{`Page ${data.page} of ${pages}`}</span>
          <span style={{ display: 'flex', gap: 8 }}>
            <SmallButton disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</SmallButton>
            <SmallButton disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Next</SmallButton>
          </span>
        </footer>
      </Card>
      <p className="body muted" style={{ marginTop: 12 }}>
        Delayed = past the latest approved completion date with no completion filing; extensions are counted separately.
        Sales % = booked ÷ (booked + unbooked) units as last reported. N/A means the source does not publish the figure or
        the project has not been crawled yet; it is never counted as zero.
      </p>
    </Page>
  )
}
