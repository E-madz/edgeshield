import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // Forward /api calls to Django so the browser sees a single origin in dev.
    proxy: {
      '/api': process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
    },
  },
})
