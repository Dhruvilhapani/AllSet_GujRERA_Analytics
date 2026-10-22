import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { Pager, TabPanel, Tabs } from './ui.jsx'

const TABS = [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }, { value: 'c', label: 'Gamma' }]

function Demo() {
  const [tab, setTab] = useState('a')
  return (
    <>
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Sections" idPrefix="t" />
      {TABS.map(t => <TabPanel key={t.value} idPrefix="t" id={t.value} active={tab === t.value}>{`content ${t.value}`}</TabPanel>)}
    </>
  )
}

describe('Tabs', () => {
  it('exposes tablist / tab / tabpanel semantics and renders only the active panel', () => {
    render(<Demo />)
    expect(screen.getByRole('tablist', { name: 'Sections' })).toBeTruthy()
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    expect(screen.getByRole('tab', { name: 'Alpha' }).getAttribute('aria-selected')).toBe('true')
    const panel = screen.getByRole('tabpanel')
    expect(panel.textContent).toBe('content a')
    expect(panel.getAttribute('aria-labelledby')).toBe('t-tab-a')
    expect(screen.getByRole('tab', { name: 'Alpha' }).getAttribute('aria-controls')).toBe('t-panel-a')
    expect(screen.queryByText('content b')).toBeNull()
  })

  it('switches on click and with the arrow keys (wrapping around)', () => {
    render(<Demo />)
    fireEvent.click(screen.getByRole('tab', { name: 'Beta' }))
    expect(screen.getByRole('tabpanel').textContent).toBe('content b')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Beta' }), { key: 'ArrowRight' })
    expect(screen.getByRole('tabpanel').textContent).toBe('content c')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Gamma' }), { key: 'ArrowRight' })
    expect(screen.getByRole('tabpanel').textContent).toBe('content a')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Alpha' }), { key: 'ArrowLeft' })
    expect(screen.getByRole('tabpanel').textContent).toBe('content c')
  })
})

describe('Pager', () => {
  it('shows the row range and disables the edges', () => {
    const seen = []
    render(<Pager page={1} pages={3} total={250} pageSize={100} noun="units" onPage={p => seen.push(p)} />)
    expect(screen.getByText(/1–100 of 250 units · page 1 of 3/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Previous' }).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(seen).toEqual([2])
  })

  it('shows 0 rows without a range', () => {
    render(<Pager page={1} pages={1} total={0} pageSize={100} noun="units" onPage={() => {}} />)
    expect(screen.getByText(/0–0 of 0 units/)).toBeTruthy()
  })
})
