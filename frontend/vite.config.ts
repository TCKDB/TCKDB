import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 4173,
    strictPort: true,
    // Tests read one repo-level guide as text (apiVocabulary.parity.test.ts); the dev server serves only the frontend.
    fs: process.env.VITEST ? { allow: ['..'] } : undefined,
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    // Some page tests take over a second alone and exceed the 5s default when the machine is loaded.
    testTimeout: 15000,
  },
})
