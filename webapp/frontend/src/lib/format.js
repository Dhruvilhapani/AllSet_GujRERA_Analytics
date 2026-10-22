/**
 * Formatting for GujRERA figures. The one rule: a value the source does not
 * publish (null / undefined) prints as "N/A" and is never coerced to 0.
 */

const NA = 'N/A'
const intFmt = new Intl.NumberFormat('en-IN')
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const isKnown = v => v !== null && v !== undefined && !(typeof v === 'number' && Number.isNaN(v))

export const fmtInt = v => (isKnown(v) ? intFmt.format(Math.round(v)) : NA)

export const fmtPct = (v, digits = 1) => (isKnown(v) ? `${Number(v).toFixed(digits)}%` : NA)

/** ISO date -> "07 Oct 2026" (parsed by hand: no timezone shift). */
export function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ''))
  return m ? `${m[3]} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : NA
}

export function fmtRupees(v) {
  if (!isKnown(v)) return NA
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} cr`
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(2)} lakh`
  return `₹${intFmt.format(v)}`
}

export function fmtDays(v) {
  if (!isKnown(v)) return NA
  if (v >= 730) return `${(v / 365).toFixed(1)} years`
  return `${intFmt.format(v)} days`
}

/** Status pill colours: reserved meanings, always shown with the label text. */
export const STATUS_TONE = {
  'Completed': { fill: 'var(--good-soft)', ink: 'var(--good)' },
  'Ongoing': { fill: 'var(--sunken)', ink: 'var(--ink-2)' },
  'Delayed': { fill: 'var(--danger-soft)', ink: 'var(--danger-ink)' },
  'Pending': { fill: 'var(--sunken)', ink: 'var(--ink-3)' },
  'Cancelled': { fill: 'var(--danger-soft)', ink: 'var(--danger-ink)' },
  'Not crawled': { fill: 'var(--amber-soft)', ink: 'var(--amber-ink)' },
  'Unknown': { fill: 'var(--sunken)', ink: 'var(--ink-3)' },
}

export const CATEGORY_LABEL = { residential: 'Residential', mixed: 'Mixed-use', commercial: 'Commercial', plotted: 'Plotted' }
export const CATEGORY_ORDER = ['residential', 'mixed', 'commercial', 'plotted']

/** Square metres. 0 prints "0": the source reports an explicit zero; only null/undefined is "N/A". */
export const fmtArea = v => (isKnown(v) ? Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 }) : NA)

/**
 * Unit booking status. The source value (BOOKED / UNBOOKED / anything else GujRERA publishes) is shown as
 * published; the normalised class only picks the colour. A missing status is "Not captured", never "unbooked".
 */
export const UNIT_STATUS_TONE = {
  booked: { fill: 'var(--accent-soft)', ink: 'var(--accent)' },
  unbooked: { fill: 'var(--sunken)', ink: 'var(--ink-2)' },
  other: { fill: '#f4ebff', ink: 'var(--violet)' },
  unknown: { fill: 'var(--amber-soft)', ink: 'var(--amber-ink)' },
}

/** Why a project's unit rows may be missing or incomplete (projects.unit_data_state). */
export const UNIT_STATE_TEXT = {
  complete: 'Unit data complete.',
  partial: 'Unit data is incomplete: some blocks could not be captured.',
  not_published: 'GujRERA publishes no unit list for this project.',
  error: 'The unit list could not be fetched from GujRERA.',
  not_fetched: 'Unit data has not been collected for this project yet.',
}

/**
 * "Last updated on GujRERA": the newest dated filing of the project. Uses the value published with the snapshot
 * (`last_rera_update`) and, for snapshots built before that existed, the newest dated event up to `today`. A future date is
 * a label, not an update, and a project with no dated event stays null (shown as N/A), never a guessed date.
 */
export function lastUpdated(project, today = new Date().toISOString().slice(0, 10)) {
  if (isKnown(project.last_rera_update)) return project.last_rera_update
  const filed = [project.form3?.submitted_on, project.portal_last_submitted_at, project.last_qpr_submitted_at]
  const dates = [...(project.events || []).map(e => e.event_date), ...filed]
    .map(d => String(d || '').slice(0, 10))
    .filter(d => d && d <= today)
    .sort()
  return dates.length ? dates[dates.length - 1] : null
}
