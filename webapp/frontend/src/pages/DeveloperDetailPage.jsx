/**
 * Developer Intelligence — one developer's GujRERA record, in this order: header, KPIs, project progress by
 * year, projects, sales, construction / extensions / delays, legal, locations, growth. Every figure comes from
 * /api/developers/{id} (server analytics layer).
 */

import { useEffect } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import { ProgressByYearChart } from '../components/charts.jsx'
import {
  ComplaintsSection, ConstructionSection, GrowthSection, RegulatorySection, LocationTable, ProjectsTable, SalesSection, SectionTitle,
} from '../components/developers.jsx'
import { DatasetStatus, Kpis, LoadError, Page, PageHeader, SkeletonRows } from '../components/ui.jsx'
import { getDeveloper } from '../lib/api.js'
import { useDataVersion } from '../lib/dataVersion.jsx'
import { CATEGORY_LABEL, fmtInt, fmtPct } from '../lib/format.js'
import useFetch from '../lib/useFetch.js'

export default function DeveloperDetailPage() {
  const { developerId } = useParams()
  const navigate = useNavigate()
  const id = Number(developerId)
  const valid = Number.isInteger(id) && id > 0
  const { meta, version, error: metaError } = useDataVersion()
  const { data: r, loading, error } = useFetch(getDeveloper, valid && version ? { id, v: version } : null)
  const back = <Link to="/developers" className="body" style={{ textDecoration: 'none', color: 'var(--ink-3)' }}>← Developers</Link>
  const show = r && r.developer?.developer_id === id
  const openProject = pid => navigate(`/projects/${pid}`)
  const { hash } = useLocation()
  useEffect(() => { // links such as /developers/12#complaints land on that section once it has rendered
    if (show && hash) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [show, hash])

  if (!show) {
    return (
      <Page>
        <PageHeader title="Developer Intelligence" back={back} />
        {!valid && <LoadError>This developer link is not valid.</LoadError>}
        {(error || (!version && metaError)) && <LoadError>{error || metaError}</LoadError>}
        {(loading || !version) && valid && !metaError && <SkeletonRows rows={8} label="Loading developer intelligence…" />}
      </Page>
    )
  }

  const dev = r.developer
  const k = r.kpis
  const partial = k.analysed < k.projects
  return (
    <Page>
      <PageHeader
        back={back}
        title={dev.name}
        subtitle={[
          meta?.district || 'GujRERA',
          ['residential', 'mixed', 'commercial', 'plotted'].filter(c => dev[`${c}_projects`]).map(c => CATEGORY_LABEL[c]).join(' / ') || null,
          dev.entity_type,
          dev.first_registration_year ? `registering with GujRERA since ${dev.first_registration_year}` : null,
        ].filter(Boolean).join(' · ')}
      />
      {r.promoter_account_ids.length > 1 && (
        <p className="body muted" style={{ marginTop: -8 }}>
          {`${r.promoter_account_ids.length} GujRERA promoter accounts merged (same ${dev.merge_basis?.includes('pan') ? 'PAN' : 'company registration number'}).`}
          {r.aliases.length > 1 ? ` Also registered as: ${r.aliases.filter(a => a !== dev.name).slice(0, 4).join('; ')}.` : ''}
        </p>
      )}
      {r.similar_names.length > 0 && (
        <p className="body" style={{ color: 'var(--amber-ink)', marginTop: -8 }}>
          {`Similar names kept separate (identity not confirmed): ${r.similar_names.slice(0, 4).map(s => s.name).join('; ')}`}
        </p>
      )}
      <DatasetStatus meta={meta} />

      <Kpis items={[
        ['Total projects', fmtInt(k.projects), partial ? `${fmtInt(k.analysed)} analysed so far` : 'all analysed'],
        ['Completed', fmtInt(k.completed), 'completion filing'],
        ['Ongoing', fmtInt(k.ongoing), 'within approved date'],
        ['Delayed', fmtInt(k.delayed), 'past approved completion date'],
        ['Avg. progress', fmtPct(k.avg_progress), 'latest report per project'],
        ['Complaints / cases', fmtInt(k.complaints), k.open_complaints ? `${fmtInt(k.open_complaints)} open` : 'filed with GujRERA'],
      ]}
      />

      <ProgressByYearChart
        title="Project progress by year"
        subtitle="Average construction progress of this developer's projects that reported in each year"
        data={r.yearly_progress}
      />

      <SectionTitle>Projects</SectionTitle>
      <ProjectsTable projects={r.projects} onOpen={openProject} />

      <SectionTitle note="Units booked and unbooked as promoters report them to GujRERA each quarter. Historical sales are not published, so no sales trend is shown.">Sales performance</SectionTitle>
      <SalesSection sales={r.sales} highlights={r.best_worst} onOpen={openProject} />

      <SectionTitle note="Delay and extension are separate: an extended project inside its new approved date is Ongoing, not Delayed.">Construction, extensions and delays</SectionTitle>
      <ConstructionSection construction={r.construction} extensions={r.extensions} delays={r.delays} onOpen={openProject} />

      <div id="complaints" style={{ scrollMarginTop: 16 }}>
        <SectionTitle>Complaints and regulatory actions</SectionTitle>
      </div>
      <ComplaintsSection complaints={r.complaints} onOpenProject={openProject} />
      <RegulatorySection regulatory={r.regulatory} events={r.events} />

      <SectionTitle>Locations</SectionTitle>
      <LocationTable locations={r.locations} />

      <SectionTitle note="From GujRERA registration dates and the locality of each registered project.">Developer growth</SectionTitle>
      <GrowthSection growth={r.growth} />
    </Page>
  )
}
