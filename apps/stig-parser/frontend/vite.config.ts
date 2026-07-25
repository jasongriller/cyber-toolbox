import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';

export default defineConfig(({ mode }) => {
  // devfull = local dev against the LIVE dev API with real login + uploads:
  // https (S3 upload CORS admits only https origins) + proxy (the REST API
  // has no CORS at all, so the browser must see same-origin API paths).
  const devfull = mode === 'devfull';
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_DEV_PROXY_TARGET; // invoke URL incl. stage, from .env.devfull.local
  return {
    plugins: [react(), ...(devfull ? [basicSsl()] : [])],
    // Assets are served from the SPA bucket through the API Gateway S3 proxy,
    // which sits under a stage path (e.g. /v1). Relative asset URLs survive that;
    // absolute ones (/assets/...) would 404.
    base: './',
    build: { outDir: 'dist', sourcemap: false },
    server: devfull
      ? {
          port: 5173,
          strictPort: true, // the uploads-bucket CORS origin names this port exactly
          proxy: target
            ? Object.fromEntries(
                ['/config', '/uploads', '/jobs'].map((p) => [
                  p,
                  { target, changeOrigin: true },
                ]),
              )
            : undefined,
        }
      : undefined,
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test-setup.ts'],
      exclude: ['tests/e2e/**', 'node_modules/**'],
    },
  };
});
