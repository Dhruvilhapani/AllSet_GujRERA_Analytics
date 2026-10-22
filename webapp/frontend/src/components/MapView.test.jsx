import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = { markers: [], icons: [], tooltips: [], view: [], removed: 0, tiles: [], groups: [], controls: [], polygons: 0 }
vi.mock('leaflet', () => {
  const chain = extra => ({ addTo() { return this }, ...extra })
  const L = {
    map: () => ({ setView(...a) { calls.view.push(a); return this }, remove() { calls.removed += 1 } }),
    tileLayer: (url, opts) => { calls.tiles.push([url, opts]); return chain({ url }) },
    layerGroup: ls => { calls.groups.push(ls.map(l => l.url)); return chain() },
    control: { layers: base => { calls.controls.push(Object.keys(base)); return chain() } },
    divIcon: opts => { calls.icons.push(opts); return opts },
    marker: (pt, opts) => {
      calls.markers.push([pt, opts.title])
      return chain({ bindTooltip(text) { calls.tooltips.push(text); return this } })
    },
    polygon: () => { calls.polygons += 1; return chain() },
  }
  return { default: L }
})

const { default: MapView } = await import('./MapView.jsx')

beforeEach(() => Object.assign(calls, { markers: [], icons: [], tooltips: [], view: [], removed: 0, tiles: [], groups: [], controls: [], polygons: 0 }))

describe('MapView', () => {
  it('says so when GujRERA has no coordinates', () => {
    render(<MapView polygon={[]} latitude={null} longitude={null} name="X" />)
    expect(screen.getByText(/no map coordinates/)).toBeTruthy()
    expect(calls.tiles).toHaveLength(0)
  })

  it('puts a red pin (no outline squares) at the published location, centred, over satellite imagery by default', async () => {
    const { unmount } = render(<MapView polygon={[[22.96, 72.64], [22.97, 72.64], [22.97, 72.65]]} latitude={22.965} longitude={72.645} name="Anmol Sky" />)
    await waitFor(() => expect(calls.markers).toHaveLength(1))
    expect(calls.markers[0]).toEqual([[22.965, 72.645], 'Anmol Sky'])
    expect(calls.polygons).toBe(0) // the polygon is not drawn
    expect(calls.view[0]).toEqual([[22.965, 72.645], 17])
    expect(calls.icons[0].html).toContain('#d93025') // red pin
    expect(calls.tooltips).toEqual(['Anmol Sky'])
    const urls = calls.tiles.map(t => t[0])
    expect(urls.some(u => u.includes('World_Imagery'))).toBe(true)
    expect(urls.some(u => u.includes('World_Boundaries_and_Places'))).toBe(true)
    expect(urls).toContain('https://tile.openstreetmap.org/{z}/{x}/{y}.png')
    expect(calls.groups[0].every(u => u.includes('arcgisonline'))).toBe(true) // satellite is the default base
    expect(calls.controls[0]).toEqual(['Satellite', 'Street'])
    expect(calls.tiles.find(t => t[0].includes('World_Imagery'))[1].attribution).toContain('Esri')
    expect(calls.tiles.find(t => t[0].includes('openstreetmap'))[1].attribution).toContain('OpenStreetMap')
    expect(screen.getByRole('region', { name: 'Map of Anmol Sky' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open in Google Maps' }).getAttribute('href')).toContain('22.965,72.645')
    unmount()
    expect(calls.removed).toBe(1) // the Leaflet map is destroyed with the component
  })

  it('uses the centre of the map record when only a polygon is published', async () => {
    render(<MapView polygon={[[23, 72], [23, 73], [24, 73], [24, 72]]} latitude={null} longitude={null} name="P" />)
    await waitFor(() => expect(calls.markers).toHaveLength(1))
    expect(calls.markers[0][0]).toEqual([23.5, 72.5])
    expect(screen.getByText(/centre of the map record/)).toBeTruthy()
  })
})
