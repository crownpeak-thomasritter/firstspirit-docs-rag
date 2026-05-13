import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Vitest config for the DynaChat frontend.
//
// Kept separate from `vite.config.ts` so the dev server and the test runner
// don't step on each other's plugin options. The `test` section controls
// Vitest; `plugins` reuses the React plugin so JSX in test files is
// transformed identically to dev/build.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/__tests__/setup.ts'],
    include: [
      'src/**/*.{test,spec}.{ts,tsx}',
      'src/__tests__/**/*.{test,spec}.{ts,tsx}',
    ],
    css: false,
  },
});
