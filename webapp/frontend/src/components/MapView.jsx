/**
 * Project location on a real map (Leaflet): a red pin at the project location, over satellite imagery (Esri World Imagery +
 * place labels, the default) or the OpenStreetMap street map, switchable on the map. The pin sits at the published
 * latitude / longitude (or, when only the map polygon is published, at the centre of that polygon). Leaflet and its
 * stylesheet are loaded on demand (own chunk), so pages without a map do not pay for them. With no coordinates the section
 * says so. Base maps come from server.arcgisonline.com (Esri) and tile.openstreetmap.org, which see the viewer's IP
 * address and the map area requested; keep the attributions.
 */

import { useEffect, useRef, useState } from 'react'

import { isKnown } from '../lib/format.js'

const STREET = {
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors',
  options: { maxZoom: 19 },
}
const SATELLITE = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
  options: { maxZoom: 20, maxNativeZoom: 18 }, // zoom 19-20 enlarges the sharpest imagery instead of showing empty tiles
}
const LABELS = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Labels &copy; Esri',
  options: { maxZoom: 20, maxNativeZoom: 18 },
}

// Red map pin (inline SVG: no image files, so it works in any bundler). The tip of the pin is the anchor.
const PIN_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="34" height="46" viewBox="0 0 34 46" aria-hidden="true">
  <path d="M17 1C8.2 1 1 8 1 16.6 1 28 17 45 17 45s16-17 16-28.4C33 8 25.8 1 17 1z" fill="#d93025" stroke="#8c1c13" stroke-width="1.5"/>
  <circle cx="17" cy="16.5" r="6" fill="#fff"/>
</svg>`
const PIN = { className: 'rera-pin', html: PIN_SVG, iconSize: [34, 46], iconAnchor: [17, 45] }
const ZOOM = 17

export default function MapView({ polygon, latitude, longitude, name }) {
  const el = useRef(null)
  const [failed, setFailed] = useState(false)
  const pts = (polygon || []).filter(p => isKnown(p[0]) && isKnown(p[1]))
  const hasPoint = isKnown(latitude) && isKnown(longitude)
  const lat = hasPoint ? latitude : pts.length ? pts.reduce((s, p) => s + p[0], 0) / pts.length : null
  const lng = hasPoint ? longitude : pts.length ? pts.reduce((s, p) => s + p[1], 0) / pts.length : null

  useEffect(() => {
    if (lat == null || !el.current) return undefined
    let map
    let cancelled = false
    Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')]).then(([mod]) => {
      if (cancelled || !el.current) return
      const L = mod.default || mod
      map = L.map(el.current, { scrollWheelZoom: false }).setView([lat, lng], ZOOM)
      const layer = base => L.tileLayer(base.url, { ...base.options, attribution: base.attribution })
      const satellite = L.layerGroup([layer(SATELLITE), layer(LABELS)]).addTo(map)
      L.control.layers({ Satellite: satellite, Street: layer(STREET) }, null, { collapsed: false }).addTo(map)
      const label = name || 'Project location'
      L.marker([lat, lng], { icon: L.divIcon(PIN), title: label, alt: label })
        .addTo(map)
        .bindTooltip(label, { direction: 'top', offset: [0, -40] })
    }).catch(() => {
      if (!cancelled) setFailed(true)
    })
    return () => {
      cancelled = true
      if (map) map.remove()
    }
  }, [lat, lng, name])

  if (lat == null) {
    return <p className="body muted" style={{ margin: 0, padding: '0 20px 16px' }}>GujRERA has no map coordinates for this project.</p>
  }
  return (
    <div style={{ padding: '0 20px 16px' }}>
      {failed
        ? <p role="alert" className="body muted" style={{ margin: '0 0 8px' }}>The map could not be loaded. Use the links below to open the location.</p>
        : <div ref={el} role="region" aria-label={`Map of ${name || 'the project'}`} style={{ height: 420, width: '100%', borderRadius: 'var(--radius-md)', border: '1px solid var(--line)', background: 'var(--sunken)', zIndex: 0 }} />}
      <p className="body muted" style={{ margin: '8px 0 0' }}>
        {`${lat.toFixed(5)}, ${lng.toFixed(5)}${hasPoint ? '' : ' (centre of the map record)'} · `}
        <a href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`} target="_blank" rel="noreferrer">Open in OpenStreetMap</a>
        {' · '}
        <a href={`https://www.google.com/maps?q=${lat},${lng}`} target="_blank" rel="noreferrer">Open in Google Maps</a>
      </p>
    </div>
  )
}
