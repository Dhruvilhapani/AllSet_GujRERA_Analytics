/**
 * Project details — one GujRERA project: identity, units, construction,
 * delays/extensions and legal records. Unpublished figures read N/A, never 0.
 */

import { Link, useParams } from 'react-router-dom'

import { ProgressHistoryChart } from '../components/charts.jsx'
import { SectionTitle } from '../components/developers.jsx'
import { Card, DataTable, Kpis, LoadError, Page, PageHeader, SkeletonRows, StatusBadge } from '../components/ui.jsx'
import { getProject } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { CATEGORY_LABEL, fmtDate, fmtDays, fmtInt, fmtPct, fmtRupees } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

function Facts({ rows }) {
  return (
    <dl style={{ margin: 0, padding: '0 20px 16px', display: 'grid', gridTemplateColumns: 'minmax(150px, 220px) 1fr', gap: '6px 16px' }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt className="body muted">{k}</dt>
          <dd className="body" style={{ margin: 0, color: 'var(--ink)', overflowWrap: 'anywhere' }}>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export default function ProjectDetailPage() {
  const { projectId } = useParams()
  const id = Number(projectId)
  const valid = Number.isInteger(id) && id > 0
  const { version, error: metaError } = useDataVersion()
  const { data: p, loading, error } = useFetch(getProject, valid && version ? { id, v: version } : null)
  const show = p && p.project_id === id
  const complaintCount = show ? (p.legal || []).filter(r => r.record_type === 'complaint').length : 0
  const back = show && p.developer_id
    ? <Link to={`/developers/${p.developer_id}`} className="body" style={{ textDecoration: 'none', color: 'var(--ink-3)' }}>{`← ${p.developer_name}`}</Link>
    : <Link to="/developers" className="body" style={{ textDecoration: 'none', color: 'var(--ink-3)' }}>← Developers</Link>

  return (
    <Page>
      {!show && <PageHeader title="Project" back={back} />}
      {!valid && <LoadError>This project link is not valid.</LoadError>}
      {(error || (!version && metaError)) && <LoadError>{error || metaError}</LoadError>}
      {(loading || !version) && !show && valid && !metaError && <SkeletonRows rows={8} label="Loading project…" />}
      {show && (
        <>
          <PageHeader back={back} title={p.name} subtitle={p.rera_number} />
          <div style={{ margin: '-6px 0 14px' }}><StatusBadge status={p.status} /></div>
          {!p.detail_crawled && <LoadError>This project&apos;s GujRERA details have not been crawled yet. Only the registration list fields are shown.</LoadError>}
          <Kpis items={[
            ['Total units', fmtInt(p.total_units)],
            ['Sold', fmtInt(p.sold_units)],
            ['Unsold', fmtInt(p.unsold_units)],
            ['Sales %', fmtPct(p.sales_pct)],
            ['Construction progress', fmtPct(p.progress_pct)],
            ['Extensions', fmtInt(p.extension_count)],
          ]}
          />
          <div className="split split-even">
            <Card title="Basic details">
              <Facts rows={[
                ['RERA number', p.rera_number || 'N/A'],
                ['Developer', p.developer_name || 'N/A'],
                ['Project type', CATEGORY_LABEL[p.category] || p.project_type || 'N/A'],
                ['Location', [p.locality, p.taluka, p.district].filter(Boolean).join(', ') || 'N/A'],
                ['Address', p.address || 'N/A'],
                ['Planning authority', p.approving_authority || 'N/A'],
                ['Registration date', fmtDate(p.registration_date)],
                ['Portal status', p.portal_status || 'N/A'],
                ['Status basis', p.status_basis || 'N/A'],
              ]}
              />
            </Card>
            <Card title="Units and construction">
              <Facts rows={[
                ['Total units', fmtInt(p.total_units)],
                ['Sold (booked)', fmtInt(p.sold_units)],
                ['Unsold (unbooked)', fmtInt(p.unsold_units)],
                ['Remaining', fmtInt(p.unsold_units)],
                ['Residential / commercial units', `${fmtInt(p.residential_units)} / ${fmtInt(p.commercial_units)}`],
                ['Latest progress', p.progress_pct != null ? `${fmtPct(p.progress_pct)} (${p.progress_quarter || 'quarter n/a'})` : 'N/A'],
                ['Financial progress', fmtPct(p.financial_progress_pct)],
                ['Construction status', p.status_detail || 'N/A'],
                ['Months since last progress report', p.months_since_last_qpr != null ? fmtInt(p.months_since_last_qpr) : 'N/A'],
                ['Project cost (declared)', fmtRupees(p.total_project_cost)],
              ]}
              />
            </Card>
          </div>

          <SectionTitle>Completion, delay and extensions</SectionTitle>
          <Card>
            <div style={{ paddingTop: 16 }}>
              <Facts rows={[
                ['Original completion date', fmtDate(p.original_completion_date)],
                ['Expected (latest approved) completion', fmtDate(p.current_completion_date)],
                ['Completion filing', p.completion_filing_date ? fmtDate(p.completion_filing_date) : 'Not filed'],
                ['Extensions', fmtInt(p.extension_count)],
                ['Total extension period', fmtDays(p.total_extension_days)],
                ['Past original date by', fmtDays(p.days_beyond_original)],
                ['Past approved date by', fmtDays(p.days_beyond_current)],
              ]}
              />
            </div>
          </Card>
          <Card title={`Approved extensions (${p.extensions?.length ?? 0})`} subtitle="As published by GujRERA. Extension reasons are not published." style={{ marginTop: 16 }}>
            <DataTable
              columns={[
                { key: 'seq', label: '#', render: e => e.seq },
                { key: 'extension_reg_no', label: 'Reference', render: e => e.extension_reg_no || 'N/A' },
                { key: 'approved_on', label: 'Approved on', render: e => fmtDate(e.approved_on) },
                { key: 'previous_completion_date', label: 'Previous completion', render: e => fmtDate(e.previous_completion_date) },
                { key: 'extended_completion_date', label: 'Extended to', render: e => fmtDate(e.extended_completion_date) },
                { key: 'extension_days', label: 'Extension', align: 'right', render: e => fmtDays(e.extension_days) },
                { key: 'status', label: 'Status', render: e => e.status || 'N/A' },
              ]}
              rows={p.extensions || []}
              rowKey={e => e.seq}
              empty="No extensions recorded for this project."
            />
          </Card>
          <div style={{ marginTop: 16 }}><ProgressHistoryChart progress={p.progress || []} /></div>

          <SectionTitle>Complaints</SectionTitle>
          <Card>
            <p className="body" style={{ margin: 0, padding: '16px 20px' }}>
              {!p.detail_crawled
                ? 'Not crawled yet.'
                : `${fmtInt(complaintCount)} complaint(s) filed against this project. `}
              {p.developer_id && p.detail_crawled
                ? <Link to={`/developers/${p.developer_id}#complaints`}>{`See all complaints for ${p.developer_name}`}</Link>
                : null}
            </p>
          </Card>
          <p className="body muted" style={{ marginTop: 16 }}>
            {`Source: GujRERA public project record${p.last_checked ? `, last checked ${fmtDate(p.last_checked)}` : ''}.`}
          </p>
        </>
      )}
    </Page>
  )
}
