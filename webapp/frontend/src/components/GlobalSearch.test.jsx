import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { searchAll } from '../lib/api.js'

import GlobalSearch, { buildItems } from './GlobalSearch.jsx'

vi.mock('../lib/api.js', () => ({ searchAll: vi.fn() }))
vi.mock('../lib/dataVersion.jsx', () => ({ useDataVersion: () => ({ version: 'v1', meta: null, error: null }) }))

const ANSWER = {
  q: 'alpha',
  projects: [
    { project_id: 1, name: 'Alpha Heights', rera_number: 'PR/1', category: 'residential', city: 'Ahmedabad', locality: 'Bopal', status: 'Ongoing', developer_name: 'Alpha Estate LLP' },
    { project_id: 2, name: 'Alpha Plaza', rera_number: 'PR/2', category: 'commercial', city: 'Ahmedabad', locality: 'SG Highway', status: 'Completed', developer_name: 'Alpha Estate LLP' },
  ],
  projects_total: 9,
  developers: [{ developer_id: 7, name: 'Alpha Estate LLP', projects_total: 2 }],
  developers_total: 1,
  localities: [{ name: 'Alphanagar', city: 'Gandhinagar', projects: 3 }],
}

function Where() {
  const l = useLocation()
  return <div data-testid="where">{`${l.pathname}${l.search}`}</div>
}
const setup = () => render(<MemoryRouter initialEntries={['/dashboard']}><GlobalSearch /><Where /></MemoryRouter>)
const type = (box, value) => fireEvent.change(box, { target: { value } })

beforeEach(() => {
  searchAll.mockReset()
  searchAll.mockResolvedValue(ANSWER)
})

describe('GlobalSearch', () => {
  it('waits for two characters before searching', async () => {
    setup()
    const box = screen.getByRole('combobox')
    type(box, 'a')
    await new Promise(r => setTimeout(r, 300))
    expect(searchAll).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('shows projects, developers and localities with counts and a see-all row', async () => {
    setup()
    type(screen.getByRole('combobox'), 'alpha')
    const list = await screen.findByRole('listbox')
    await waitFor(() => expect(within(list).getAllByRole('option').length).toBe(5))
    expect(searchAll).toHaveBeenCalledWith({ q: 'alpha', limit: 6, v: 'v1' })
    const opts = within(list).getAllByRole('option').map(o => o.textContent)
    expect(opts[0]).toContain('Alpha Heights')
    expect(opts[0]).toContain('Residential · PR/1 · Bopal, Ahmedabad · Ongoing')
    expect(opts[0]).toContain('by Alpha Estate LLP')
    expect(opts[2]).toBe('See all 9 matching projects')
    expect(opts[3]).toContain('2 projects')
    expect(opts[4]).toContain('Gandhinagar · 3 projects')
    expect(within(list).getByText('Projects')).toBeTruthy()
    expect(within(list).getByText('Developers')).toBeTruthy()
    expect(within(list).getByText('Localities')).toBeTruthy()
    expect(within(list).getAllByText('Alpha', { selector: 'mark' }).length).toBeGreaterThan(0) // matched words are highlighted
  })

  it('opens a suggestion with the keyboard (arrows + Enter) and by click', async () => {
    setup()
    const box = screen.getByRole('combobox')
    type(box, 'alpha')
    await screen.findByRole('listbox')
    await waitFor(() => expect(screen.getAllByRole('option').length).toBe(5))
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    expect(box.getAttribute('aria-activedescendant')).toContain('p2')
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(screen.getByTestId('where').textContent).toBe('/projects/2')
    expect(screen.queryByRole('listbox')).toBeNull()

    type(box, 'alpha ')
    type(box, 'alpha')
    await screen.findByRole('listbox')
    await waitFor(() => expect(screen.getAllByRole('option').length).toBe(5))
    fireEvent.click(screen.getAllByRole('option').find(o => o.id.endsWith('-d7'))) // the developer row
    expect(screen.getByTestId('where').textContent).toBe('/developers/7')
  })

  it('Enter without a highlighted row lists every matching project; see-all and locality rows carry filters', async () => {
    setup()
    const box = screen.getByRole('combobox')
    type(box, 'alpha heights')
    await waitFor(() => expect(searchAll).toHaveBeenCalled())
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(screen.getByTestId('where').textContent).toBe('/projects?q=alpha%20heights')

    const items = buildItems(ANSWER, 'alpha')
    expect(items.find(i => i.key === 'p-all').to).toBe('/projects?q=alpha')
    expect(items.find(i => i.group === 'Localities').to).toBe('/projects?locality=Alphanagar&city=Gandhinagar')
    expect(buildItems(ANSWER, 'alpha').some(i => i.key === 'd-all')).toBe(false) // all developers already shown
  })

  it('says so when nothing matches, and Escape closes the list', async () => {
    searchAll.mockResolvedValue({ q: 'zzzz', projects: [], projects_total: 0, developers: [], developers_total: 0, localities: [] })
    setup()
    const box = screen.getByRole('combobox')
    type(box, 'zzzz')
    expect((await screen.findAllByText(/No projects, developers or localities match/)).length).toBeGreaterThan(0)
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('"/" focuses the search box from anywhere', () => {
    setup()
    fireEvent.keyDown(window, { key: '/' })
    expect(document.activeElement).toBe(screen.getByRole('combobox'))
  })
})
