import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Lab backend on :8002; override with VITE_PROXY_TARGET for isolated stacks.
const proxyTarget =
  (process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8002").replace("localhost", "127.0.0.1");

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
