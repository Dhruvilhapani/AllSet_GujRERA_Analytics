import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { FacilitiesCards } from './amenities.jsx'

const item = (over = {}) => ({
  item_key: 'internalRoad', label: 'Internal roads and footpaths', category: 'roads', source: 'form_one_common', applicable: 'YES',
  progress_pct: 70, planned_start: '2015-12-15', planned_end: '2025-01-25', actual_start: null, actual_end: null, remarks: null,
  form1_submitted_on: '2026-09-11', ...over,
})
const club = item({ item_key: 'other:1', label: 'Club House', category: 'named_by_promoter', source: 'form_one_other', progress_pct: 100, remarks: 'COMPLETED CLUB HOUSE', planned_end: '2022-12-31' })

describe('FacilitiesCards', () => {
  it('lists promoter-named amenities and the common facilities with status and progress', () => {
    render(<FacilitiesCards amenities={[club, item(), item({ item_key: 'security', label: 'Security', category: 'safety_security', applicable: 'NO', progress_pct: 0 }), item({ item_key: 'sewerage', label: 'Sewerage', category: 'sewage_waste', applicable: null, progress_pct: null })]} />)
    const tables = screen.getAllByRole('table')
    const named = within(tables[0]).getAllByRole('row').slice(1)
    expect(named).toHaveLength(1)
    expect(named[0].textContent).toContain('Club House')
    expect(named[0].textContent).toContain('100%')
    expect(named[0].textContent).toContain('COMPLETED CLUB HOUSE')
    const rows = within(tables[1]).getAllByRole('row').slice(1).map(r => within(r).getAllByRole('cell').map(c => c.textContent))
    const byLabel = Object.fromEntries(rows.map(r => [r[1], r]))
    expect(byLabel['Internal roads and footpaths'].slice(2, 4)).toEqual(['Included', '70%'])
    expect(byLabel.Security.slice(2, 4)).toEqual(['Not applicable', '—'])        // no progress for a facility that is not part of the project
    expect(byLabel.Sewerage.slice(2, 4)).toEqual(['Not stated', '—'])            // unknown, neither provided nor absent
    expect(screen.getByText(/1 of 3 included/)).toBeTruthy()
  })

  it('says so when no extra amenity is named and when nothing was collected', () => {
    const { unmount } = render(<FacilitiesCards amenities={[item()]} />)
    expect(screen.getByText(/has not named any extra amenity/)).toBeTruthy()
    unmount()
    render(<FacilitiesCards amenities={[]} />)
    expect(screen.getByText(/No Form 1 facility declaration has been collected/)).toBeTruthy()
  })
})
