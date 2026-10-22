/**
 * Project details — one GujRERA project, organised in tabs (the tab is kept in the URL: ?tab=units):
 * Overview, Developer, Location & map, Amenities & infrastructure, Construction & approvals, Blocks & units.
 * Unpublished figures read N/A, never 0.
 */

import { Link, useParams, useSearchParams } from 'react-router-dom'

import { FacilitiesCards } from '../components/amenities.jsx'
import { ProgressHistoryChart } from '../components/charts.jsx'
import { LegalTable, SectionTitle, Timeline } from '../components/developers.jsx'
import MapView from '../components/MapView.jsx'
import { UnitsTab } from '../components/units.jsx'
import { Badge, Card, DataTable, Empty, Kpis, LoadError, Page, PageHeader, SkeletonRows, StatusBadge, TabPanel, Tabs } from '../components/ui.jsx'
import { getProject } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { CATEGORY_LABEL, fmtArea, fmtDate, fmtDays, fmtInt, fmtPct, fmtRupees, isKnown, lastUpdated } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

const TAB_PREFIX = 'project'
const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'developer', label: 'Developer' },
  { value: 'location', label: 'Location & map' },
  { value: 'amenities', label: 'Amenities & infrastructure' },
  { value: 'construction', label: 'Construction & approvals' },
  { value: 'units', label: 'Blocks & units' },
]

const INFRA_GROUPS = [
  ['external_development', 'External development (who provides what)'],
  ['water_sewage', 'Water, sewage and waste'],
  ['nearby', 'Near-by facilities (distances as declared)'],
  ['other_development', 'Other development'],
]
const ROLE_LABEL = { engineer: 'Engineer', architect: 'Architect', contractor: 'Contractor', chartered_accountant: 'Chartered accountant', agent: 'Agent' }

const humanize = s => String(s || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
const yesNo = v => (isKnown(v) ? String(v) : 'N/A')

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

function Overview({ p }) {
  return (
    <>
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
            ['City / location', [p.locality, p.taluka, p.city].filter(Boolean).join(', ') || 'N/A'],
            ['Address', p.address || 'N/A'],
            ['Planning authority', p.approving_authority || 'N/A'],
            ['Registration date', fmtDate(p.registration_date)],
            ['Start date (declared)', fmtDate(p.start_date)],
            ['Last updated', fmtDate(lastUpdated(p))],
            ['Portal status', p.portal_status || 'N/A'],
            ['Status basis', p.status_basis || 'N/A'],
          ]}
          />
        </Card>
        <Card title="Units and construction" subtitle="Quarterly-report figures. The unit-by-unit list is on the Blocks & units tab.">
          <Facts rows={[
            ['Total units', fmtInt(p.total_units)],
            ['Sold (booked)', fmtInt(p.sold_units)],
            ['Unsold (unbooked)', fmtInt(p.unsold_units)],
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
      {p.project_description && (
        <Card title="Project description (as registered)" style={{ marginTop: 16 }}>
          <p className="body" style={{ margin: 0, padding: '0 20px 16px', whiteSpace: 'pre-line', color: 'var(--ink)' }}>{p.project_description}</p>
        </Card>
      )}
    </>
  )
}

function DeveloperTab({ p }) {
  const byRole = {}
  for (const r of p.professionals || []) (byRole[r.role] ||= []).push(r.name)
  const d = p.developer || {}
  return (
    <>
      <Card title="Developer / promoter" subtitle="Company details as registered with GujRERA. Contact details are not published here.">
        <Facts rows={[
          ['Promoter', p.developer_id
            ? <Link to={`/developers/${p.developer_id}`}>{p.developer_name}</Link>
            : (p.developer_name || 'N/A')],
          ['Promoter type', d.entity_type || 'N/A'],
          ['Registration status', d.registration_status || 'N/A'],
          ['Company / firm registration no.', d.company_reg_number || 'N/A'],
          ['Date of incorporation', fmtDate(d.date_of_incorporation)],
          ['Registered office', d.registered_address || 'N/A'],
          ['Website', d.website ? <a href={/^https?:/i.test(d.website) ? d.website : `https://${d.website}`} target="_blank" rel="noreferrer">{d.website}</a> : 'N/A'],
          ['Group', d.declared_group_name || 'N/A'],
          ['Declared experience', d.declared_experience_years ? `${d.declared_experience_years} years in Gujarat` : 'N/A'],
          ['Declared projects (total / completed / ongoing)', `${fmtInt(d.declared_total_projects)} / ${fmtInt(d.declared_completed_projects)} / ${fmtInt(d.declared_ongoing_projects)}`],
          ['Partners / directors (names)', d.associates?.length ? d.associates.join(', ') : 'N/A'],
          ['Developer report', p.developer_id ? <Link to={`/developers/${p.developer_id}`}>Open the full report (all projects, complaints, delays)</Link> : 'N/A'],
        ]}
        />
      </Card>
      <Card title="Professionals on the project" subtitle="Names and roles as listed in the GujRERA registration." style={{ marginTop: 16 }}>
        {Object.keys(byRole).length
          ? <Facts rows={Object.entries(byRole).map(([role, names]) => [ROLE_LABEL[role] || humanize(role), names.join(', ')])} />
          : <Empty>No professionals are listed for this project.</Empty>}
      </Card>
    </>
  )
}

function LocationTab({ p }) {
  return (
    <div className="split split-even">
      <Card title="Location and land identification">
        <Facts rows={[
          ['City / district', [p.city, p.district && p.district !== p.city ? p.district : null].filter(Boolean).join(' · ') || 'N/A'],
          ['Taluka', p.taluka || 'N/A'],
          ['Locality (moje)', p.locality || p.village_moje || 'N/A'],
          ['Pincode', p.pincode || 'N/A'],
          ['Address', p.address || 'N/A'],
          ['Planning authority', p.planning_authority || p.approving_authority || 'N/A'],
          ['Revenue survey no.', yesNo(p.survey_number)],
          ['City survey no.', yesNo(p.city_survey_number)],
          ['TP scheme', [p.tp_number && `No. ${p.tp_number}`, p.tp_name].filter(Boolean).join(' · ') || 'N/A'],
          ['Final plot (FP) no.', yesNo(p.fp_number)],
          ['Land area', isKnown(p.total_land_area_sqm) ? `${fmtArea(p.total_land_area_sqm)} sq m` : 'N/A'],
        ]}
        />
      </Card>
      <Card title="Map" subtitle="Project location from GujRERA's map record.">
        <MapView polygon={p.polygon} latitude={p.latitude} longitude={p.longitude} name={p.name} />
      </Card>
    </div>
  )
}

function AmenitiesTab({ p }) {
  const groups = INFRA_GROUPS.map(([key, title]) => [title, (p.infrastructure || []).filter(r => r.category === key)]).filter(([, rows]) => rows.length)
  return (
    <>
      <FacilitiesCards amenities={p.amenities || []} />
      <Card title="Areas and parking" subtitle="As declared at registration." style={{ marginTop: 16 }}>
        <Facts rows={[
          ['Total land area', isKnown(p.total_land_area_sqm) ? `${fmtArea(p.total_land_area_sqm)} sq m` : 'N/A'],
          ['Carpet area (project)', isKnown(p.total_carpet_area_sqm) ? `${fmtArea(p.total_carpet_area_sqm)} sq m` : 'N/A'],
          ['Covered area', isKnown(p.total_covered_area_sqm) ? `${fmtArea(p.total_covered_area_sqm)} sq m` : 'N/A'],
          ['Open area', isKnown(p.total_open_area_sqm) ? `${fmtArea(p.total_open_area_sqm)} sq m` : 'N/A'],
          ['Parking for sale', fmtInt(p.parking_for_sale)],
          ['Covered parking', fmtInt(p.covered_parking)],
          ['Garages for sale', fmtInt(p.garages_for_sale)],
        ]}
        />
      </Card>
      {groups.map(([title, rows]) => (
        <Card key={title} title={title} style={{ marginTop: 16 }}>
          <Facts rows={rows.map(r => [humanize(r.name), r.value])} />
        </Card>
      ))}
      <p className="body muted" style={{ marginTop: 12 }}>
        GujRERA has no separate amenity list. This tab shows the facilities declared in Form 1 (including any the promoter
        names itself, such as a club house) and the infrastructure declared at registration. A facility that is not listed
        was not declared; that is not the same as absent.
      </p>
    </>
  )
}

function ConstructionTab({ p }) {
  const complaintCount = (p.legal || []).filter(r => r.record_type === 'complaint').length
  return (
    <>
      <Card title="Completion, delay and extensions">
        <div style={{ paddingTop: 4 }}>
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

      <SectionTitle>Approvals, certificates and filings</SectionTitle>
      <Card subtitle="Official documents linked from the GujRERA record (opens the portal's file).">
        <DataTable
          columns={[
            { key: 'document_type', label: 'Document', render: d => humanize(d.document_type) },
            { key: 'document_name', label: 'Reference', render: d => <span style={{ whiteSpace: 'normal' }}>{d.document_name || 'N/A'}</span> },
            { key: 'document_date', label: 'Date', render: d => fmtDate(d.document_date) },
            { key: 'url', label: 'File', render: d => <a href={d.url} target="_blank" rel="noreferrer">Open</a> },
          ]}
          rows={p.documents || []}
          rowKey={d => `${d.document_type}-${d.url}`}
          empty="No certificates or filings are linked for this project."
        />
      </Card>
      {p.form3 && (
        <Card title="Latest Form 3 (quarterly booking filing)" style={{ marginTop: 16 }}>
          <Facts rows={[
            ['Submitted on', fmtDate(p.form3.submitted_on)],
            ['Units declared in the form', fmtInt(p.form3.number_of_units)],
            ['Residential / shop / office / other', `${fmtInt(p.form3.total_residential)} / ${fmtInt(p.form3.total_shop)} / ${fmtInt(p.form3.total_office)} / ${fmtInt(p.form3.total_others)}`],
          ]}
          />
        </Card>
      )}

      <SectionTitle>Complaints and regulatory records</SectionTitle>
      <Card>
        <p className="body" style={{ margin: 0, padding: '16px 20px' }}>
          {!p.detail_crawled
            ? 'Not crawled yet.'
            : `${fmtInt(complaintCount)} complaint(s) filed against this project. `}
          {p.developer_id && p.detail_crawled
            ? <Link to={`/developers/${p.developer_id}#complaints`}>{`See all complaints for ${p.developer_name}`}</Link>
            : null}
        </p>
        {p.detail_crawled && (p.legal || []).length > 0 && <LegalTable records={p.legal} showProject={false} />}
      </Card>
      <SectionTitle>Timeline</SectionTitle>
      <Card><Timeline events={p.events || []} showProject={false} /></Card>
    </>
  )
}

export default function ProjectDetailPage() {
  const { projectId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const id = Number(projectId)
  const valid = Number.isInteger(id) && id > 0
  const { version, error: metaError } = useDataVersion()
  const { data: p, loading, error } = useFetch(getProject, valid && version ? { id, v: version } : null)
  const show = p && p.project_id === id
  const tab = TABS.some(t => t.value === searchParams.get('tab')) ? searchParams.get('tab') : 'overview'
  const setTab = value => setSearchParams((prev) => {
    const next = new URLSearchParams(prev)
    if (value === 'overview') next.delete('tab')
    else next.set('tab', value)
    return next
  }, { replace: true })
  const back = show && p.developer_id
    ? <Link to={`/developers/${p.developer_id}`} className="body" style={{ textDecoration: 'none', color: 'var(--ink-3)' }}>{`← ${p.developer_name}`}</Link>
    : <Link to="/projects" className="body" style={{ textDecoration: 'none', color: 'var(--ink-3)' }}>← Projects</Link>

  return (
    <Page>
      {!show && <PageHeader title="Project" back={back} />}
      {!valid && <LoadError>This project link is not valid.</LoadError>}
      {(error || (!version && metaError)) && <LoadError>{error || metaError}</LoadError>}
      {(loading || !version) && !show && valid && !metaError && <SkeletonRows rows={8} label="Loading project…" />}
      {show && (
        <>
          <PageHeader back={back} title={p.name} subtitle={p.rera_number} />
          <div style={{ margin: '-6px 0 14px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <StatusBadge status={p.status} />
            <Badge>{CATEGORY_LABEL[p.category] || p.project_type || 'Project'}</Badge>
            {p.city && <Badge>{p.city}</Badge>}
          </div>
          <Tabs tabs={TABS} value={tab} onChange={setTab} label="Project sections" idPrefix={TAB_PREFIX} />
          <TabPanel idPrefix={TAB_PREFIX} id="overview" active={tab === 'overview'}><Overview p={p} /></TabPanel>
          <TabPanel idPrefix={TAB_PREFIX} id="developer" active={tab === 'developer'}><DeveloperTab p={p} /></TabPanel>
          <TabPanel idPrefix={TAB_PREFIX} id="location" active={tab === 'location'}><LocationTab p={p} /></TabPanel>
          <TabPanel idPrefix={TAB_PREFIX} id="amenities" active={tab === 'amenities'}><AmenitiesTab p={p} /></TabPanel>
          <TabPanel idPrefix={TAB_PREFIX} id="construction" active={tab === 'construction'}><ConstructionTab p={p} /></TabPanel>
          <TabPanel idPrefix={TAB_PREFIX} id="units" active={tab === 'units'}><UnitsTab project={p} version={version} /></TabPanel>
          <p className="body muted" style={{ marginTop: 16 }}>
            {`Source: GujRERA public project record${p.last_checked ? `, last checked ${fmtDate(p.last_checked)}` : ''}.`}
          </p>
        </>
      )}
    </Page>
  )
}
