import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = 'http://localhost:8001';

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'build'
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    // Set VITE_ALLOWED_HOSTS to a comma-separated list when serving the dev server
    // behind a proxy or tunnel. Empty by default: localhost needs no entry.
    allowedHosts: (process.env.VITE_ALLOWED_HOSTS || '').split(',').filter(Boolean),
    historyApiFallback: true,
    // Mirrors production path routing so dev and prod resolve URLs identically.
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/feed': { target: backend, changeOrigin: true },
      '/fever': { target: backend, changeOrigin: true },
    }
  },
});
