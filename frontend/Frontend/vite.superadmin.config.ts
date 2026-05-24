import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Serve superadmin.html at the dev-server root so http://localhost:5174/
// loads the SuperAdmin app instead of the default index.html (main app).
const serveSuperAdminAtRoot = {
  name: 'superadmin-root',
  configureServer(server: { middlewares: { use: (fn: (req: { url?: string }, res: unknown, next: () => void) => void) => void } }) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/' || req.url === '/index.html') {
        req.url = '/superadmin.html'
      }
      next()
    })
  },
}

export default defineConfig({
  plugins: [react(), serveSuperAdminAtRoot],
  server: {
    port: 5174,
    strictPort: true,
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist-superadmin',
    rollupOptions: {
      input: 'superadmin.html',
    },
  },
})
