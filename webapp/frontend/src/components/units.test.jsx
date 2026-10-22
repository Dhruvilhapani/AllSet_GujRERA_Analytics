import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getUnits } from '../lib/api.js'

import { UnitsTab } from './units.jsx'

vi.mock('../lib/api.js', () => ({ getUnits: vi.fn() }))

const BLOCKS = [
  { block_name: 'A', sold_units: 3, unsold_units: 1, unit_rows: 4, unit_booked: 2, unit_unbooked: 1, unit_other: 1, fetch_outcome: 'ok' },
  { block_name: 'B', sold_units: 1, unsold_units: 1, unit_rows: 2, unit_booked: 1, unit_unbooked: 0, unit_other: 1, fetch_outcome: 'ok' },
]
const project = (over = {}) => ({
  project_id: 1, category: 'residential', unit_data_state: 'complete', unit_data_note: null, units_as_of: '2026-09-30',
  units_row_count: 6, units_available: true, blocks: BLOCKS, ...over,
})
const unit = (over = {}) => ({
  block_name: 'A', unit_no: '101', unit_kind: 'single', usage: 'Residential', usage_class: 'residential', carpet_area_sqm: 40,
  area_excl_balcony_terrace_sqm: 2, status: 'BOOKED', status_norm: 'booked', agreement_date: '2024-05-01', encumbrance_status: 'Released',
  redeveloped: 'NO', ...over,
})
const page = (units, over = {}) => ({
  project_id: 1, available: true, units, total: units.length, page: 1, page_size: 100, usage_classes: ['residential'],
  summary: { booked: 1, unbooked: 1, other: 1, unknown: 1, total: 4 }, ...over,
})

beforeEach(() => getUnits.mockReset())

describe('UnitsTab', () => {
  it('shows the published status, N/A for unknown values and 0 for a published zero', async () => {
    getUnits.mockResolvedValue(page([
      unit(),
      unit({ unit_no: '1001', status: 'UNBOOKED', status_norm: 'unbooked', area_excl_balcony_terrace_sqm: 0, agreement_date: null }),
      unit({ unit_no: '20', status: 'SOLD', status_norm: 'other', carpet_area_sqm: null, area_excl_balcony_terrace_sqm: null }),
      unit({ unit_no: '7', status: null, status_norm: 'unknown' }),
    ]))
    render(<UnitsTab project={project()} version="v1" initialView="table" />)
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(4)
    const cells = r => within(r).getAllByRole('cell').map(c => c.textContent)
    expect(cells(rows[0])).toEqual(['101', 'A', 'Residential', '40', '2', 'BOOKED', '01 May 2024', 'Released', 'NO'])
    expect(cells(rows[1])[4]).toBe('0')                 // published zero stays 0
    expect(cells(rows[1])[6]).toBe('N/A')               // unknown date
    expect(cells(rows[2]).slice(3, 6)).toEqual(['N/A', 'N/A', 'SOLD'])   // unknown areas; status exactly as published
    expect(cells(rows[3])[5]).toBe('Not captured')      // a missing status is never shown as unbooked
  })

  it('draws both booking sources per block and flags when they differ', async () => {
    getUnits.mockResolvedValue(page([unit()]))
    render(<UnitsTab project={project()} version="v1" initialView="table" />)
    await screen.findByRole('table')
    const a = screen.getByRole('button', { name: /Block A/ })
    expect(a.textContent).toContain('4 units')
    expect(a.textContent).toContain('Portal booking chart: 3 booked / 1 unbooked')
    expect(a.textContent).toContain('differs from the unit rows')
    expect(screen.getByRole('button', { name: /Block B/ }).textContent).toContain('Portal booking chart: 1 booked / 1 unbooked')
  })

  it('filters by block and status through the API parameters', async () => {
    getUnits.mockResolvedValue(page([unit()]))
    render(<UnitsTab project={project()} version="v1" initialView="table" />)
    await screen.findByRole('table')
    fireEvent.click(screen.getByRole('button', { name: /Block B/ }))
    await waitFor(() => expect(getUnits).toHaveBeenLastCalledWith(expect.objectContaining({ block: 'B', page: 1 })))
    fireEvent.click(screen.getByRole('button', { name: /Unbooked · 1/ }))
    await waitFor(() => expect(getUnits).toHaveBeenLastCalledWith(expect.objectContaining({ block: 'B', status: 'unbooked' })))
  })

  it('never lists units when the project data was not captured, and says why', () => {
    render(<UnitsTab project={project({ unit_data_state: 'not_fetched', blocks: [], units_row_count: null })} version="v1" initialView="table" />)
    expect(screen.getByRole('status').textContent).toContain('Unit data has not been collected')
    expect(screen.getByRole('status').textContent).toContain('not counted as unbooked')
    expect(screen.queryByRole('table')).toBeNull()
    expect(getUnits).not.toHaveBeenCalled()
  })

  it('warns that a partial project only lists the captured blocks', async () => {
    getUnits.mockResolvedValue(page([unit()]))
    render(<UnitsTab project={project({ unit_data_state: 'partial', unit_data_note: 'unit lists missing for blocks: B' })} version="v1" initialView="table" />)
    await screen.findByRole('table')
    expect(screen.getByRole('status').textContent).toContain('incomplete')
    expect(screen.getByRole('status').textContent).toContain('missing for blocks: B')
    expect(screen.getByText('Only the captured blocks are listed.')).toBeTruthy()
  })

  it('says so when the unit file is not part of the build', () => {
    render(<UnitsTab project={project({ units_available: false })} version="v1" initialView="table" />)
    expect(screen.getByRole('alert').textContent).toContain('not part of this build')
    expect(getUnits).not.toHaveBeenCalled()
  })

  it('uses plot wording and no building columns for plotted projects', async () => {
    getUnits.mockResolvedValue(page([unit({ usage: 'Other', usage_class: 'plot', unit_no: 'P-12' })]))
    render(<UnitsTab project={project({ category: 'plotted' })} version="v1" initialView="table" />)
    await screen.findByRole('table')
    const headers = screen.getAllByRole('columnheader').map(h => h.textContent.replace(/ [▲▼]$/, ''))
    expect(headers).toContain('Plot no.')
    expect(headers).toContain('Plot area (sq m)')
    expect(headers.some(h => /balcony|Usage|Carpet|Redevelopment/.test(h))).toBe(false)
  })

  it('marks promoter-filed ranges instead of presenting them as single units', async () => {
    getUnits.mockResolvedValue(page([unit({ unit_no: 'A - 101 to 904', unit_kind: 'range' })]))
    render(<UnitsTab project={project()} version="v1" initialView="table" />)
    await screen.findByRole('table')
    expect(screen.getByText('(range)')).toBeTruthy()
  })
})

describe('UnitGrid (block grid view)', () => {
  const gridPage = [
    unit({ unit_no: '101' }),
    unit({ unit_no: '102', status: 'UNBOOKED', status_norm: 'unbooked', agreement_date: null }),
    unit({ unit_no: '103', agreement_date: null }),
    unit({ unit_no: '201', block_name: 'B', status: null, status_norm: 'unknown', agreement_date: null }),
  ]

  it('is the default view and lists every unit of every block with status and date', async () => {
    getUnits.mockResolvedValue(page(gridPage))
    render(<UnitsTab project={project()} version="v1" />)
    const a = await screen.findByRole('region', { name: 'Block A' })
    const b = screen.getByRole('region', { name: 'Block B' })
    expect(screen.queryByRole('table')).toBeNull()
    expect(within(a).getByRole('button', { name: 'unit 101, Booked, 01 May 2024' })).toBeTruthy()
    expect(within(a).getByRole('button', { name: 'unit 102, Unbooked' })).toBeTruthy()
    expect(within(a).getByRole('button', { name: 'unit 103, Booked, Date not disclosed' })).toBeTruthy()   // booked, no date: never guessed
    expect(within(b).getByRole('button', { name: 'unit 201, Not captured' })).toBeTruthy()                  // never drawn as unbooked
    expect(a.textContent).toContain('3 units')
    expect(a.textContent).toContain('2 booked')
    expect(a.textContent).toContain('1 unbooked')
    expect(getUnits).toHaveBeenCalledWith(expect.objectContaining({ id: 1, page_size: 500 }))
  })

  it('opens the full published details of a unit on click', async () => {
    getUnits.mockResolvedValue(page(gridPage))
    render(<UnitsTab project={project()} version="v1" />)
    fireEvent.click(await screen.findByRole('button', { name: 'unit 101, Booked, 01 May 2024' }))
    const d = screen.getByRole('region', { name: 'Selected unit' })
    expect(d.textContent).toContain('Carpet area (sq m)')
    expect(d.textContent).toContain('40')
    expect(d.textContent).toContain('01 May 2024')
    expect(d.textContent).toContain('Released')
    fireEvent.click(within(d).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Selected unit' })).toBeNull()
  })

  it('filters tiles by status, by search and by the block card without refetching', async () => {
    getUnits.mockResolvedValue(page(gridPage))
    render(<UnitsTab project={project()} version="v1" />)
    await screen.findByRole('region', { name: 'Block A' })
    fireEvent.click(screen.getByRole('button', { name: /Unbooked · 1/ }))
    expect(screen.queryByRole('button', { name: /unit 101/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'unit 102, Unbooked' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /All · 4/ }))
    fireEvent.change(screen.getByLabelText('Search unit number'), { target: { value: '103' } })
    expect(screen.getAllByRole('button', { name: /^unit / })).toHaveLength(1)
    fireEvent.change(screen.getByLabelText('Search unit number'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: /^Block B/ }))
    expect(screen.queryByRole('region', { name: 'Block A' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Block B' })).toBeTruthy()
    expect(getUnits).toHaveBeenCalledTimes(1)
  })

  it('says when a block has no captured unit rows instead of showing it as unbooked', async () => {
    getUnits.mockResolvedValue(page([unit({ unit_no: '101' })]))
    const blocks = [...BLOCKS, { block_name: 'C', unit_rows: null, fetch_outcome: 'not_fetched' }]
    render(<UnitsTab project={project({ unit_data_state: 'partial', blocks })} version="v1" />)
    const c = await screen.findByRole('region', { name: 'Block C' })
    expect(c.textContent).toContain('not captured')
    expect(c.textContent).toContain('not counted as unbooked')
  })

  it('switches to the table view', async () => {
    getUnits.mockResolvedValue(page(gridPage))
    render(<UnitsTab project={project()} version="v1" />)
    await screen.findByRole('region', { name: 'Block A' })
    fireEvent.click(screen.getByRole('button', { name: 'Table' }))
    expect(await screen.findByRole('table')).toBeTruthy()
  })
})
