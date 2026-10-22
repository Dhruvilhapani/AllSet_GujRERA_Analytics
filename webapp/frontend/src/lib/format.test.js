import { describe, expect, it } from 'vitest'

import { fmtArea, fmtDate, fmtInt, fmtPct, lastUpdated, UNIT_STATE_TEXT } from './format.js'

describe('missing is never zero', () => {
  it('prints N/A for unknown values and 0 for a published zero', () => {
    expect(fmtArea(null)).toBe('N/A')
    expect(fmtArea(undefined)).toBe('N/A')
    expect(fmtArea(Number.NaN)).toBe('N/A')
    expect(fmtArea(0)).toBe('0')
    expect(fmtInt(null)).toBe('N/A')
    expect(fmtInt(0)).toBe('0')
    expect(fmtPct(null)).toBe('N/A')
    expect(fmtPct(0)).toBe('0.0%')
  })

  it('formats areas with Indian digit grouping and at most two decimals', () => {
    expect(fmtArea(1390.744)).toBe('1,390.74')
    expect(fmtArea(2.5)).toBe('2.5')
  })

  it('parses ISO timestamps with an offset without shifting the date', () => {
    expect(fmtDate('2019-10-26T00:00:00.000+0530')).toBe('26 Oct 2019')
    expect(fmtDate(null)).toBe('N/A')
  })

  it('has a message for every unit data state', () => {
    for (const s of ['complete', 'partial', 'not_published', 'error', 'not_fetched']) expect(UNIT_STATE_TEXT[s]).toBeTruthy()
  })
})

describe('lastUpdated', () => {
  const ev = d => ({ event_date: d })
  it('prefers the published value, else the newest dated event up to today', () => {
    expect(lastUpdated({ last_rera_update: '2026-09-30', events: [ev('2026-10-01')] }, '2026-10-22')).toBe('2026-09-30')
    expect(lastUpdated({ events: [ev('2018-01-01'), ev('2026-07-15'), ev('2027-03-01')] }, '2026-10-22')).toBe('2026-07-15')
    expect(lastUpdated({ events: [ev('2026-10-19T00:00:00')] }, '2026-10-22')).toBe('2026-10-19')
  })
  it('counts the latest Form 3 (unit booking) filing, as on project 21058', () => {
    const p = { events: [ev('2024-03-19')], form3: { submitted_on: '2026-10-07T00:00:00' } }
    expect(lastUpdated(p, '2026-10-22')).toBe('2026-10-07')
    expect(lastUpdated({ events: [ev('2024-03-19')], form3: { submitted_on: '2027-01-01' } }, '2026-10-22')).toBe('2024-03-19')
  })
  it('is null (N/A) when there is no dated event, never a guess', () => {
    expect(lastUpdated({ events: [] })).toBeNull()
    expect(lastUpdated({})).toBeNull()
    expect(lastUpdated({ events: [{ event_date: null }, ev('2099-01-01')] }, '2026-10-22')).toBeNull()
    expect(fmtDate(lastUpdated({}))).toBe('N/A')
  })
})
