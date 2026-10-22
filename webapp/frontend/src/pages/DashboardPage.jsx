/**
 * Dashboard — Ahmedabad market overview. All metrics come from /api/dashboard (server analytics layer);
 * this page only lays them out. Inventory (units / sold / unsold) lives at project level, not here.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ColumnChart, ProgressByYearChart, SERIES, StatusBar } from '../components/charts.jsx'
import { Card, DataTable, DatasetStatus, Kpis, LoadError, Page, PageHeader, Segmented, SkeletonRows } from '../components/ui.jsx'
import { getDashboard } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { CATEGORY_LABEL, CATEGORY_ORDER, fmtInt, fmtPct, fmtRupees } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

export default function DashboardPage() {
  const navigate = useNavigate()
  const { meta: m, version, error: metaError } = useDataVersion()
  const [city, setCity] = useState('')
  const [category, setCategory] = useState('')
  const { data: d, loading, error } = useFetch(getDashboard, version ? { v: version, city, category } : null)
  const scope = m?.dataset_complete ? '' : ' · current dataset'
  const k = d?.kpis
  const cities = m?.cities || []
  const categories = m?.categories || []
  // one chart series per category the data actually has (residential / mixed always, to keep the shape stable)
  const present = d ? CATEGORY_ORDER.filter(c => c === 'residential' || c === 'mixed' || (d.registrations_by_year || []).some(r => r[c] != null)) : []
  const where = [city, category ? CATEGORY_LABEL[category] : null].filter(Boolean).join(' · ')

  return (
    <Page>
      <PageHeader title="Dashboard" subtitle={`${cities.join(' + ') || m?.district || 'GujRERA'} market · ${m?.scope || 'Projects registered with GujRERA'}${where ? ` · filtered: ${where}` : ''}`} />
      {(error || metaError) && <LoadError>{error || metaError}</LoadError>}
      <DatasetStatus meta={m} />
      {(cities.length > 1 || categories.length > 1) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, margin: '0 0 16px' }}>
          {cities.length > 1 && <Segmented label="City" value={city} onChange={setCity} options={[{ value: '', label: 'All cities' }, ...cities.map(c => ({ value: c, label: c }))]} />}
          {categories.length > 1 && <Segmented label="Project category" value={category} onChange={setCategory} options={[{ value: '', label: 'All types' }, ...categories.map(c => ({ value: c, label: CATEGORY_LABEL[c] || c }))]} />}
        </div>
      )}
      {(loading || !m) && !d && !metaError && <SkeletonRows rows={10} label="Loading dashboard…" />}
      {d && (
        <>
          <Kpis items={[
            ['Registered projects', fmtInt(k.projects), `${fmtInt(k.analysed)} analysed`],
            ['Developers', fmtInt(k.developers), 'in analysed projects'],
            ['Completed', fmtInt(k.completed), 'completion filing (Form 4)'],
            ['Delayed', fmtInt(k.delayed), 'past approved completion date'],
            ['Avg. project progress', fmtPct(k.avg_progress), 'latest quarterly report'],
            ['Complaints', fmtInt(k.complaints), 'linked to analysed projects'],
          ]}
          />

          <div className="split split-even">
            <ProgressByYearChart title="Market project progress by year" subtitle={`Average reported construction progress of projects reporting each year${scope}`} data={d.yearly_progress} />
            <ColumnChart
              title="Project registrations by year"
              subtitle="All registered projects, by GujRERA approval year"
              rows={d.registrations_by_year}
              xKey="year"
              series={present.map(c => ({ key: c, label: CATEGORY_LABEL[c], color: SERIES[CATEGORY_ORDER.indexOf(c)] }))}
              empty="No registrations."
            />
          </div>

          <div className="split split-even" style={{ marginTop: 16 }}>
            <Card title="Project status" subtitle={`Analysed projects${scope}. Extensions are tracked separately and are not a status.`}>
              <StatusBar counts={d.status} note={d.status.stalled ? `${fmtInt(d.status.stalled)} ongoing projects show no recent progress (stalled).` : null} />
            </Card>
            <ColumnChart
              title="Sales % across projects"
              subtitle="Projects by share of units booked (projects that publish booking data)"
              rows={d.sales_distribution}
              xKey="band"
              ordinal
              series={[{ key: 'projects', label: 'Projects', color: SERIES[0] }]}
              empty="No booking data yet."
              height={190}
            />
          </div>

          <h2 className="section-title" style={{ margin: '28px 0 12px' }}>Developers</h2>
          <Card title="Largest developers" subtitle="By registered projects. Click a developer for the full profile.">
            <DataTable
              columns={[
                { key: 'name', label: 'Developer', render: r => <span style={{ color: 'var(--ink)', fontWeight: 550, whiteSpace: 'normal' }}>{r.name}</span> },
                { key: 'projects_total', label: 'Projects', align: 'right', render: r => fmtInt(r.projects_total) },
                { key: 'completion_rate_pct', label: 'Completion rate', align: 'right', render: r => fmtPct(r.completion_rate_pct, 0) },
                { key: 'avg_progress_pct', label: 'Avg. progress', align: 'right', render: r => fmtPct(r.avg_progress_pct, 0) },
                { key: 'delayed', label: 'Delayed', align: 'right', render: r => fmtInt(r.delayed) },
                { key: 'total_extensions', label: 'Extensions', align: 'right', render: r => fmtInt(r.total_extensions) },
                { key: 'complaints_total', label: 'Complaints', align: 'right', render: r => fmtInt(r.complaints_total) },
              ]}
              rows={d.leaderboard}
              rowKey={r => r.developer_id}
              onRowClick={r => navigate(`/developers/${r.developer_id}`)}
              rowLabel={r => `Open ${r.name}`}
              empty="No developers identified yet."
            />
          </Card>

          <div className="split split-even" style={{ marginTop: 16 }}>
            <Card title="Localities" subtitle={`Most active areas (GujRERA locality field)${d.projects_without_locality ? `; ${fmtInt(d.projects_without_locality)} projects publish no locality` : ''}`}>
              <DataTable
                columns={[
                  { key: 'area', label: 'Area', render: r => <span style={{ color: 'var(--ink)' }}>{r.area}</span> },
                  { key: 'projects', label: 'Projects', align: 'right', render: r => fmtInt(r.projects) },
                  { key: 'avg_progress', label: 'Avg. progress', align: 'right', render: r => fmtPct(r.avg_progress, 0) },
                  { key: 'delayed', label: 'Delayed', align: 'right', render: r => fmtInt(r.delayed) },
                ]}
                rows={d.localities}
                rowKey={r => r.area}
                empty="No locality data yet."
              />
            </Card>
            <Card title="Legal and regulatory" subtitle="GujRERA records linked to analysed projects. A complaint is a filing, not a finding.">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12, padding: '0 20px 12px' }}>
                {[['Complaints', fmtInt(d.legal.complaints)], ['Suo-motu proceedings', fmtInt(d.legal.suo_motu)], ['Penalty challans', fmtInt(d.legal.challans)], ['Penalties levied', fmtRupees(d.legal.penalty_amount)]].map(([l, v]) => (
                  <div key={l}>
                    <div className="body muted">{l}</div>
                    <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>{v}</div>
                  </div>
                ))}
              </div>
              <DataTable
                columns={[
                  { key: 'category', label: 'Complaint type' },
                  { key: 'records', label: 'Complaints', align: 'right', render: r => fmtInt(r.records) },
                ]}
                rows={d.legal.categories}
                rowKey={r => r.category}
                empty="No complaints recorded."
              />
            </Card>
          </div>
        </>
      )}
    </Page>
  )
}
