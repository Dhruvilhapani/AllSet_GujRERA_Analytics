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

export const CATEGORY_LABEL = { residential: 'Residential', mixed: 'Mixed-use' }
