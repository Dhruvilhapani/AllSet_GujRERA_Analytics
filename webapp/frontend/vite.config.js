import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs separately (uvicorn on :8600); in production the
// API serves the built files itself, so /api is always same-origin.
export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', globals: true, include: ['src/**/*.test.{js,jsx}'] },
  server: {
    port: 5180,
    proxy: { '/api': 'http://127.0.0.1:8600' },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: { 'vendor-react': ['react', 'react-dom', 'react-router-dom'], 'vendor-charts': ['recharts'] },
      },
    },
  },
})
