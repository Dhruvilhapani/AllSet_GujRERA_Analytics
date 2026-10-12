import { Suspense, lazy } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { DataVersionProvider } from './lib/dataVersion.jsx'

const DashboardPage = lazy(() => import('./pages/DashboardPage.jsx'))
const DevelopersPage = lazy(() => import('./pages/DevelopersPage.jsx'))
const DeveloperDetailPage = lazy(() => import('./pages/DeveloperDetailPage.jsx'))
const ProjectDetailPage = lazy(() => import('./pages/ProjectDetailPage.jsx'))

const NAV = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/developers', label: 'Developers' },
]

function Sidebar() {
  return (
    <aside style={{
      position: 'fixed', inset: '0 auto 0 0', width: 'var(--sidebar)', background: 'var(--card)',
      borderRight: '1px solid var(--line)', padding: '20px 12px', display: 'flex', flexDirection: 'column', gap: 4,
    }}
    >
      <div style={{ padding: '0 10px 18px' }}>
        <div style={{ fontWeight: 700, color: 'var(--ink)', fontSize: 15 }}>RERA Analytics</div>
        <div className="body muted">Ahmedabad · GujRERA</div>
      </div>
      <nav aria-label="Main" style={{ display: 'grid', gap: 4 }}>
        {NAV.map(n => (
          <NavLink
            key={n.to}
            to={n.to}
            style={({ isActive }) => ({
              display: 'block', padding: '8px 12px', borderRadius: 'var(--radius-md)', textDecoration: 'none',
              fontWeight: 550, fontSize: 13.5,
              color: isActive ? 'var(--accent)' : 'var(--ink-2)',
              background: isActive ? 'var(--accent-soft)' : 'transparent',
            })}
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
      <p className="body muted" style={{ marginTop: 'auto', padding: '0 10px' }}>
        Source: GujRERA public project records.
      </p>
    </aside>
  )
}

function PageLoading() {
  return <div role="status" className="body muted" style={{ padding: 32 }}>Loading…</div>
}

function NotFound() {
  return <div className="body muted" style={{ padding: 32 }}>Page not found.</div>
}

export default function App() {
  return (
    <BrowserRouter>
      <DataVersionProvider>
        <Sidebar />
        <main style={{ marginLeft: 'var(--sidebar)', minHeight: '100%' }}>
          <Suspense fallback={<PageLoading />}>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/developers" element={<DevelopersPage />} />
              <Route path="/developers/:developerId" element={<DeveloperDetailPage />} />
              <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </main>
      </DataVersionProvider>
    </BrowserRouter>
  )
}
