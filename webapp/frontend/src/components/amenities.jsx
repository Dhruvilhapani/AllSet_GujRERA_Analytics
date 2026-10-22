/**
 * Facilities and amenities as declared in the project's Form 1 (architect's certificate): the common facilities
 * with applicability and % of work done, and any facility the promoter names itself (e.g. "Club House").
 * GujRERA has no other amenity list: a facility that is not listed was not declared, which is not the same as absent.
 */

import { fmtDate, fmtPct, isKnown } from '../lib/format.js'

import { Badge, Card, DataTable, Empty } from './ui.jsx'

const CATEGORY_TEXT = {
  roads: 'Roads and lighting',
  water: 'Water',
  sewage_waste: 'Sewage, drainage and waste',
  electrical: 'Electrical',
  energy: 'Energy',
  fire_safety: 'Fire and emergency',
  safety_security: 'Safety and security',
  landscaping: 'Landscaping',
  community: 'Community',
  structure: 'Structure',
  other: 'Other',
}
const CATEGORY_ORDER = Object.keys(CATEGORY_TEXT)

function Applicability({ value }) {
  if (value === 'YES') return <Badge fill="var(--accent-soft)" ink="var(--accent)">Included</Badge>
  if (value === 'NO') return <Badge>Not applicable</Badge>
  return <Badge fill="var(--amber-soft)" ink="var(--amber-ink)">Not stated</Badge>
}

const progress = r => (r.applicable === 'YES' && isKnown(r.progress_pct) ? fmtPct(r.progress_pct, 0) : '—')

export function FacilitiesCards({ amenities }) {
  const named = amenities.filter(a => a.source === 'form_one_other')
  const common = amenities
    .filter(a => a.source === 'form_one_common')
    .sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category) || a.label.localeCompare(b.label))
  const asOf = amenities.find(a => a.form1_submitted_on)?.form1_submitted_on
  const included = common.filter(a => a.applicable === 'YES').length

  if (!amenities.length) {
    return (
      <Card title="Facilities and amenities">
        <Empty>No Form 1 facility declaration has been collected for this project.</Empty>
      </Card>
    )
  }
  return (
    <>
      <Card
        title={`Amenities named by the promoter (${named.length})`}
        subtitle="Extra facilities the promoter lists in Form 1, such as a club house, with their reported progress."
      >
        <DataTable
          columns={[
            { key: 'label', label: 'Amenity', render: r => <b style={{ color: 'var(--ink)' }}>{r.label}</b> },
            { key: 'progress_pct', label: 'Work done', align: 'right', render: r => progress(r) },
            { key: 'planned_end', label: 'Planned completion', render: r => fmtDate(r.planned_end) },
            { key: 'actual_end', label: 'Actual completion', render: r => fmtDate(r.actual_end) },
            { key: 'remarks', label: 'Remarks', render: r => <span style={{ whiteSpace: 'normal' }}>{r.remarks || 'N/A'}</span> },
          ]}
          rows={named}
          rowKey={r => r.item_key}
          empty="The promoter has not named any extra amenity (such as a club house) in Form 1."
        />
      </Card>
      <Card
        title={`Common facilities and infrastructure (${included} of ${common.length} included)`}
        subtitle={`As certified in Form 1${asOf ? `, submitted ${fmtDate(asOf)}` : ''}. "Not applicable" means the architect marked the item as not part of the project.`}
        style={{ marginTop: 16 }}
      >
        <DataTable
          columns={[
            { key: 'category', label: 'Group', render: r => CATEGORY_TEXT[r.category] || r.category },
            { key: 'label', label: 'Facility', render: r => <span style={{ whiteSpace: 'normal', color: 'var(--ink)' }}>{r.label}</span> },
            { key: 'applicable', label: 'In project', render: r => <Applicability value={r.applicable} /> },
            { key: 'progress_pct', label: 'Work done', align: 'right', render: r => progress(r) },
            { key: 'planned_end', label: 'Planned completion', render: r => (r.applicable === 'YES' ? fmtDate(r.planned_end) : '—') },
            { key: 'actual_end', label: 'Actual completion', render: r => (r.applicable === 'YES' ? fmtDate(r.actual_end) : '—') },
            { key: 'remarks', label: 'Remarks', render: r => <span style={{ whiteSpace: 'normal' }}>{r.remarks || 'N/A'}</span> },
          ]}
          rows={common}
          rowKey={r => r.item_key}
          empty="No common facilities were declared."
        />
      </Card>
    </>
  )
}
